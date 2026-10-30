"""ML service — face embedding clustering for face library audit.

流程（非监督学习，无需标注）：
    Milvus 拉取全部向量
      -> L2 归一化
      -> N x N 余弦相似度矩阵
      -> 层次聚类 linkage(average, cosine) + fcluster(距离阈值)
      -> 异常检测：
           · 同一学生、相似度 < SAME_STUDENT_THRESHOLD  => 脏数据（可能录错人）
           · 不同学生、相似度 > CROSS_STUDENT_THRESHOLD => 重复注册（同一个人多开账号）
           · 聚类簇内混入多个不同学生  => 强证据的重复注册
"""
from __future__ import annotations

import numpy as np
from scipy.cluster.hierarchy import linkage, fcluster
from sqlalchemy.orm import Session

from core.milvus import backend_name
from models.student import Student
from repository import face_repo

# ---- 阈值标定 ----
# ArcFace(buffalo_l) 的 normed_embedding 做余弦相似度时，实测经验区间：
#   同一人的不同照片：0.45 ~ 0.65
#   不同人：          -0.1 ~ 0.15
# 因此判定边界取 0.4~0.5，而不是常见的 0.8+（那是未归一化向量或别的模型才成立）。
# 可用 GET /api/audit/similarity-matrix 看自己数据的真实分布再微调。
SAME_STUDENT_THRESHOLD = 0.40    # 同一学号内，相似度低于此值 => 判为脏数据
# 跨学号判重阈值。论文 5.5 节以验证集双指标复核确定的部署值（15/15 命中、正常库零误报），
# 覆盖原工程默认值 0.50（该值仅命中 14/15）。与 same_student=0.40 数值相同但语义不同：
# 本项管"跨学号判重"，same_student 管"同一学号内脏数据"，二者独立。
CROSS_STUDENT_THRESHOLD = 0.40
CLUSTER_DISTANCE_THRESHOLD = 0.60  # 余弦距离阈值(1-相似度)，约等于相似度 0.40 聚成一簇


def _empty_result(backend: str, face_count: int, reason: str = "") -> dict:
    return {"code": 0, "data": {
        "backend": backend,
        "face_count": face_count,
        "total_vectors": face_count,
        "clusters": [],
        "suspicious_clusters": [],
        "duplicates": [],
        "dirty_data": [],
        "summary": {
            "total_vectors": face_count,
            "cluster_count": 0,
            "duplicate_pairs": 0,
            "dirty_pairs": 0,
            "suspicious_cluster_count": 0,
            "students_involved": 0,
        },
        "note": reason,
    }}


def similarity_matrix(db: Session, top_pairs: int = 30) -> dict:
    """导出相似度矩阵，便于按真实数据标定阈值。

    返回 labels（face_id / student_no）+ matrix（N x N 余弦相似度）+ top_pairs。
    """
    try:
        vectors = face_repo.get_all_vectors()
    except Exception as exc:  # noqa: BLE001
        return {"code": 1, "message": f"vector store unavailable: {exc}", "data": None}

    if not vectors:
        return {"code": 0, "data": {"backend": backend_name(), "labels": [], "matrix": [], "top_pairs": []}}

    face_ids = [int(v["face_id"]) for v in vectors]
    student_ids = [int(v["student_id"]) for v in vectors]
    embeddings = np.asarray([v["embedding"] for v in vectors], dtype=np.float64)
    normalized = embeddings / (np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-8)
    sim = normalized @ normalized.T

    labels = []
    for fid, sid in zip(face_ids, student_ids):
        s = db.get(Student, sid)
        labels.append({
            "index": len(labels),
            "face_id": fid,
            "student_id": sid,
            "name": s.name if s else "?",
            "student_no": s.student_no if s else "?",
        })

    pairs = []
    n = len(face_ids)
    for i in range(n):
        for j in range(i + 1, n):
            pairs.append({
                "i": i, "j": j,
                "face_a": face_ids[i], "face_b": face_ids[j],
                "student_a": labels[i]["student_no"], "student_b": labels[j]["student_no"],
                "same_student": student_ids[i] == student_ids[j],
                "similarity": round(float(sim[i, j]), 4),
            })
    pairs.sort(key=lambda x: x["similarity"], reverse=True)

    return {"code": 0, "data": {
        "backend": backend_name(),
        "count": n,
        "labels": labels,
        "matrix": [[round(float(v), 4) for v in row] for row in sim],
        "top_pairs": pairs[:top_pairs],
    }}


def audit_duplicates(
    db: Session,
    same_student_threshold: float = SAME_STUDENT_THRESHOLD,
    cross_student_threshold: float = CROSS_STUDENT_THRESHOLD,
    cluster_distance: float = CLUSTER_DISTANCE_THRESHOLD,
) -> dict:
    """Run face library audit: dirty data + duplicate registration.

    返回结构固定（即使向量不足），方便前端与联调脚本稳定消费。
    """
    backend = backend_name()
    try:
        all_vectors = face_repo.get_all_vectors()
    except Exception as exc:  # noqa: BLE001
        # 向量库异常也返回统一结构，避免向上抛 500
        result = _empty_result(backend, 0, f"向量库读取失败: {type(exc).__name__}: {exc}")
        result["code"] = 1
        result["message"] = "vector store unavailable"
        return result

    n = len(all_vectors)

    if n == 0:
        return _empty_result(backend, 0, "脸库为空，暂无可审计的向量")
    if n == 1:
        return _empty_result(backend, 1, "仅 1 条向量，无法进行两两比对")

    face_ids = [int(v["face_id"]) for v in all_vectors]
    student_ids = [int(v["student_id"]) for v in all_vectors]
    embeddings = np.asarray([v["embedding"] for v in all_vectors], dtype=np.float64)

    # L2 归一化后，点积即余弦相似度
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    normalized = embeddings / (norms + 1e-8)
    sim_matrix = normalized @ normalized.T

    # ---------- 层次聚类（非监督） ----------
    clusters: list[dict] = []
    suspicious_clusters: list[dict] = []
    try:
        # 余弦距离矩阵 -> 平均连接层次聚类
        condensed = 1.0 - sim_matrix
        np.fill_diagonal(condensed, 0.0)
        # linkage 需要压缩上三角
        from scipy.spatial.distance import squareform
        z = linkage(squareform(condensed, checks=False), method="average")
        labels = fcluster(z, t=cluster_distance, criterion="distance")
        for cid in sorted(set(labels.tolist())):
            members = [i for i, lab in enumerate(labels.tolist()) if lab == cid]
            sids = sorted({student_ids[i] for i in members})
            cluster = {
                "cluster_id": int(cid),
                "size": len(members),
                "student_ids": sids,
                "face_ids": [face_ids[i] for i in members],
                "mixed_students": len(sids) > 1,
            }
            clusters.append(cluster)
            if len(sids) > 1:
                # 簇内跨学生：比两两相似度更强的重复注册证据
                details = []
                for sid in sids:
                    s = db.get(Student, sid)
                    details.append({
                        "id": sid,
                        "name": s.name if s else "?",
                        "student_no": s.student_no if s else "?",
                    })
                cluster["students"] = details
                suspicious_clusters.append(cluster)
    except Exception as exc:  # noqa: BLE001
        # 聚类失败不影响两两比对结果
        clusters = []
        suspicious_clusters = []
        note = f"clustering skipped: {type(exc).__name__}: {exc}"
    else:
        note = ""

    # ---------- 检查 1：同一学生、低相似度 = 脏数据 ----------
    dirty_data = []
    for i in range(n):
        for j in range(i + 1, n):
            if student_ids[i] != student_ids[j]:
                continue
            sim = float(sim_matrix[i, j])
            if sim < same_student_threshold:
                s = db.get(Student, student_ids[i])
                dirty_data.append({
                    "student": {
                        "id": student_ids[i],
                        "name": s.name if s else "?",
                        "student_no": s.student_no if s else "?",
                    },
                    "face_a_id": face_ids[i],
                    "face_b_id": face_ids[j],
                    "similarity": round(sim, 4),
                    "issue": "same student, low similarity — possible wrong face recorded",
                })

    # ---------- 检查 2：不同学生、高相似度 = 重复注册 ----------
    duplicates = []
    for i in range(n):
        for j in range(i + 1, n):
            if student_ids[i] == student_ids[j]:
                continue
            sim = float(sim_matrix[i, j])
            if sim > cross_student_threshold:
                sa = db.get(Student, student_ids[i])
                sb = db.get(Student, student_ids[j])
                duplicates.append({
                    "student_a": {
                        "id": student_ids[i],
                        "name": sa.name if sa else "?",
                        "student_no": sa.student_no if sa else "?",
                    },
                    "student_b": {
                        "id": student_ids[j],
                        "name": sb.name if sb else "?",
                        "student_no": sb.student_no if sb else "?",
                    },
                    "similarity": round(sim, 4),
                })

    involved = sorted({sid for c in suspicious_clusters for sid in c["student_ids"]}
                      | {d["student"]["id"] for d in dirty_data}
                      | {d["student_a"]["id"] for d in duplicates}
                      | {d["student_b"]["id"] for d in duplicates})

    return {"code": 0, "data": {
        "backend": backend,
        "face_count": n,
        "total_vectors": n,
        "thresholds": {
            "same_student": same_student_threshold,
            "cross_student": cross_student_threshold,
            "cluster_distance": cluster_distance,
        },
        "clusters": clusters,
        "suspicious_clusters": suspicious_clusters,
        "duplicates": duplicates,
        "dirty_data": dirty_data,
        "summary": {
            "total_vectors": n,
            "cluster_count": len(clusters),
            "duplicate_pairs": len(duplicates),
            "dirty_pairs": len(dirty_data),
            "suspicious_cluster_count": len(suspicious_clusters),
            "students_involved": len(involved),
        },
        "note": note,
    }}
