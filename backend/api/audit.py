"""Audit controller — ML face library audit (clustering results)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from api.deps import get_db_session
from service import cluster_service

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/duplicates", summary="脸库审计：双通道查重（脏数据 + 重复注册 + 可疑簇）")
def audit_duplicates(
    same_student: float = Query(cluster_service.SAME_STUDENT_THRESHOLD, ge=0.0, le=1.0,
                                description="同一学生相似度下限(低于则判脏数据)"),
    cross_student: float = Query(cluster_service.CROSS_STUDENT_THRESHOLD, ge=0.0, le=1.0,
                                 description="不同学生相似度上限(高于则判重复注册)"),
    cluster_distance: float = Query(cluster_service.CLUSTER_DISTANCE_THRESHOLD, gt=0.0, le=2.0,
                                    description="层次聚类余弦距离阈值"),
    db: Session = Depends(get_db_session),
):
    """Face library audit: detect dirty data and duplicate registrations.

    阈值可通过查询参数在线调整，便于按实际数据标定：
        /api/audit/duplicates?cross_student=0.45
    """
    return cluster_service.audit_duplicates(
        db,
        same_student_threshold=same_student,
        cross_student_threshold=cross_student,
        cluster_distance=cluster_distance,
    )


@router.get("/similarity-matrix", summary="全库两两相似度矩阵（用于标定阈值）")
def similarity_matrix(db: Session = Depends(get_db_session)):
    """返回全部向量的余弦相似度矩阵（用于阈值标定 / 调参）。"""
    return cluster_service.similarity_matrix(db)
