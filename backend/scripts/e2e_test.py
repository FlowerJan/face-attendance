"""端到端联调脚本：覆盖第一阶段全部接口

用法（服务已启动在 8000 端口）：
    .venv/Scripts/python.exe scripts/e2e_test.py
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import urllib.error
import urllib.request

import cv2
import numpy as np

BASE = "http://127.0.0.1:8000"
SAMPLE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_faces")
IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp")
passed: list[str] = []
failed: list[str] = []


def call(method: str, path: str, payload=None, form: bytes | None = None, headers=None):
    url = BASE + path
    data = None
    hdrs = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode()
        hdrs["Content-Type"] = "application/json"
    elif form is not None:
        data = form
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return {"code": exc.code, "message": exc.read().decode()[:300], "data": None}


def check(name: str, cond: bool, detail: str = "") -> None:
    (passed if cond else failed).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (f" -- {detail}" if detail else ""))


def fetch_bytes(path: str) -> tuple[int, bytes]:
    """直接取裸资源（用于验证静态文件挂载）。"""
    try:
        with urllib.request.urlopen(BASE + path, timeout=15) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except Exception:  # noqa: BLE001
        return 0, b""


def synth_face(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    img[:, :] = (210, 220, 235)
    cv2.ellipse(img, (320, 250), (110, 140), 0, 0, 360, (158, 182, 218), -1)
    cv2.circle(img, (280, 220), 14, (60, 70, 90), -1)
    cv2.circle(img, (360, 220), 14, (60, 70, 90), -1)
    cv2.ellipse(img, (320, 320), (45, 22), 0, 0, 180, (90, 80, 110), 6)
    img = np.clip(img.astype(np.int16) + rng.integers(-18, 18, img.shape, dtype=np.int16), 0, 255).astype(np.uint8)
    return cv2.imencode(".jpg", img)[1].tobytes()


def multipart(field: str, filename: str, content: bytes) -> tuple[bytes, dict]:
    boundary = "----e2eboundary" + str(random.randint(1000, 9999))
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode(),
        b"Content-Type: image/jpeg\r\n\r\n",
        content,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    return body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}


async def ws_test() -> None:
    """公告 WebSocket 实时推送验证"""
    try:
        import websockets
    except ImportError:
        check("WebSocket 推送（跳过：缺少 websockets 客户端）", True, "skipped")
        return

    received: list[dict] = []
    async with websockets.connect("ws://127.0.0.1:8000/ws/notices") as ws:
        await ws.send(json.dumps({"type": "ping"}))
        pong = await asyncio.wait_for(ws.recv(), timeout=5)
        check("WebSocket 心跳 pong", "pong" in pong, pong)

        # 通过 HTTP 发布公告，期待 WS 收到推送
        payload = {"title": f"联调公告 {random.randint(100, 999)}", "content": "WebSocket 推送验证", "publisher": "e2e"}
        resp = call("POST", "/api/notices", payload)
        check("发布公告(HTTP)", resp.get("code") == 0, resp.get("message", ""))

        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=6)
            msg = json.loads(raw)
            received.append(msg)
        except asyncio.TimeoutError:
            msg = {}
        check("WS 收到实时推送", msg.get("type") == "notice" and msg.get("data", {}).get("title") == payload["title"],
              json.dumps(msg, ensure_ascii=False)[:120])


def real_face_phase(enums: dict) -> None:
    """真实人脸全链路：检测 -> 向量入库 -> Milvus 计数 -> 聚类审计。

    样本放在 scripts/sample_faces/，命名约定 <姓名>__<第几张>.jpg，
    同名前缀的图片会被聚类到同一簇（用于验证聚类是否真的生效）。
    """
    print("\n[8] 真实人脸入库 + 聚类审计")
    if not os.path.isdir(SAMPLE_DIR):
        check("真实人脸样本（跳过：缺 scripts/sample_faces/）", True, "skipped")
        return
    files = sorted(f for f in os.listdir(SAMPLE_DIR) if f.lower().endswith(IMG_EXT))
    if not files:
        check("真实人脸样本（跳过：目录为空）", True, "skipped")
        return
    print(f"      样本数: {len(files)} -> {', '.join(files)}")

    base_vec = int((call("GET", "/api/system/status").get("data") or {}).get("vector_count") or 0)
    created_ids: list[int] = []
    detected_count = 0
    enrolled_count = 0
    gate_rejects: list[str] = []
    for fname in files:
        person = os.path.splitext(fname)[0].split("__")[0]
        no = f"RF{random.randint(100000, 999999)}"
        created = call("POST", "/api/students", {
            "student_no": no,
            "name": person,
            "gender": "男",
            "class_name": (enums.get("classes") or ["智造本2024-1班"])[0],
            "major": (enums.get("majors") or ["计算机科学与技术"])[0],
            "enroll_year": 2024,
        })
        sid = (created.get("data") or {}).get("id")
        if not sid:
            check(f"建学生 {person} ({fname})", False, str(created.get("message"))[:80])
            continue
        created_ids.append(sid)

        with open(os.path.join(SAMPLE_DIR, fname), "rb") as fp:
            content = fp.read()
        body, hdrs = multipart("file", fname, content)
        r = call("POST", f"/api/students/{sid}/faces", form=body, headers=hdrs)
        data = r.get("data") or {}
        hit = data.get("detected") is True
        # 质量门控会在检测通过、画质不达标时拒绝入库：
        # 此时 detected=True 但 enrolled=False，两者必须分开断言，
        # 否则「检测到但被门控拒收」会被误当成录入成功。
        enrolled_ok = data.get("enrolled") is True
        detected_count += 1 if hit else 0
        enrolled_count += 1 if enrolled_ok else 0
        if hit and not enrolled_ok:
            gate_rejects.append(f"{fname}:{data.get('rule', '-')}")
        check(f"录入 {fname} 检测到人脸", hit,
              (data.get("reason") or r.get("message") or "")[:60])

    if not created_ids:
        return

    if not created_ids:
        return

    # ---- 静态资源挂载：脸照 URL 必须能取回真实 JPEG ----
    first_faces = call("GET", f"/api/students/{created_ids[0]}/faces").get("data") or []
    if first_faces:
        up = first_faces[0]["image_path"]
        code, body = fetch_bytes(up)
        check("脸照静态URL可访问且为JPEG", code == 200 and body[:2] == b"\xff\xd8",
              f"{up} -> HTTP {code}, {len(body)}B")
        stu = call("GET", f"/api/students/{created_ids[0]}").get("data") or {}
        check("录入后 face_status 置为已录入", stu.get("face_status") == 1,
              f"face_status={stu.get('face_status')}")

    # ---- Phase B：故意把两个「不同人」的照片录到同一个学号下，验证脏数据检出 ----
    same_person = [f for f in files if f.split("__")[0] == "alpha"]
    other_person = [f for f in files if f.split("__")[0] != "alpha"]
    if same_person and other_person:
        created = call("POST", "/api/students", {
            "student_no": f"MX{random.randint(100000, 999999)}",
            "name": "混录测试",
            "gender": "男",
            "class_name": (enums.get("classes") or ["智造本2024-1班"])[0],
            "major": (enums.get("majors") or ["计算机科学与技术"])[0],
            "enroll_year": 2024,
        })
        mixed_id = (created.get("data") or {}).get("id")
        if mixed_id:
            created_ids.append(mixed_id)
            for fname in (same_person[0], other_person[0]):
                with open(os.path.join(SAMPLE_DIR, fname), "rb") as fp:
                    body, hdrs = multipart("file", fname, fp.read())
                call("POST", f"/api/students/{mixed_id}/faces", form=body, headers=hdrs)
            check("混录两名不同人（用于脏数据检测）", True,
                  f"{same_person[0]} + {other_person[0]} -> 同一学号")

    check("真实人脸至少检出 1 张", detected_count > 0, f"{detected_count}/{len(files)}")

    # 质量门控：真实样本应全部通过门控并入库。
    # 若有被拒，说明阈值定得太严 —— 真实合规素材被误伤了。
    check("真实人脸全部通过质量门控并入库", enrolled_count == len(files),
          f"入库 {enrolled_count}/{len(files)}"
          + (f"，被拒：{', '.join(gate_rejects)}" if gate_rejects else ""))

    st = call("GET", "/api/system/status").get("data") or {}
    check("真实向量已写入向量库", int(st.get("vector_count") or 0) >= enrolled_count,
          f"vector_count={st.get('vector_count')}, 入库={enrolled_count}")

    audit = call("GET", "/api/audit/duplicates").get("data") or {}
    sm = audit.get("summary") or {}
    print("      审计:", json.dumps({
        "backend": audit.get("backend"),
        "face_count": audit.get("face_count"),
        "cluster_count": sm.get("cluster_count"),
        "duplicate_pairs": sm.get("duplicate_pairs"),
        "dirty_pairs": sm.get("dirty_pairs"),
        "suspicious_clusters": sm.get("suspicious_cluster_count"),
    }, ensure_ascii=False))
    check("聚类生效（cluster_count >= 1）", int(sm.get("cluster_count") or 0) >= 1,
          f"clusters={sm.get('cluster_count')}")
    check("审计返回 face_count 与实际向量数一致",
          int(audit.get("face_count") or 0) == int(st.get("vector_count") or 0),
          f"{audit.get('face_count')} vs {st.get('vector_count')}")
    check("同一人多学号 -> 检出重复注册",
          int(sm.get("duplicate_pairs") or 0) >= 1,
          f"duplicate_pairs={sm.get('duplicate_pairs')}")
    check("同一学号混入他人 -> 检出脏数据",
          int(sm.get("dirty_pairs") or 0) >= 1,
          f"dirty_pairs={sm.get('dirty_pairs')}")
    check("检出跨学生可疑簇",
          int(sm.get("suspicious_cluster_count") or 0) >= 1,
          f"suspicious_clusters={sm.get('suspicious_cluster_count')}")
    check("审计结构完整（backend/summary/clusters）",
          bool(audit.get("backend")) and "cluster_count" in sm and isinstance(audit.get("clusters"), list),
          f"backend={audit.get('backend')}")

    # 清理测试数据（级联删向量 + 文件）
    last_del: dict = {}
    for sid in created_ids:
        last_del = call("DELETE", f"/api/students/{sid}")
    check("级联删除返回清理数量", "deleted_vectors" in (last_del.get("data") or {}),
          json.dumps(last_del.get("data") or {}, ensure_ascii=False)[:110])

    st2 = call("GET", "/api/system/status").get("data") or {}
    check("清理后向量回到基线", int(st2.get("vector_count") or 0) == base_vec,
          f"剩余 vector_count={st2.get('vector_count')} (基线 {base_vec})")

    fc = call("GET", "/api/stats/face-count").get("data") or {}
    check("无孤儿脸照（SQLite 外键级联生效）", int(fc.get("orphan_faces") or 0) == 0,
          f"orphan_faces={fc.get('orphan_faces')}")
    check("统计不出现负数", int(fc.get("zero_face") or 0) >= 0,
          f"zero_face={fc.get('zero_face')}")

    # total_faces 是「条数」而非「人数」——曾因误用 one+multiple 导致 HTTP 取数
    # 模式把 7（人数）当成条数，与直连模式的 12 对不上。这里锁死口径：
    # total_faces 必须等于向量库条数（清理后二者都应回到基线）。
    st3 = call("GET", "/api/system/status").get("data") or {}
    total_faces = int(fc.get("total_faces") or 0)
    vec_after = int(st3.get("vector_count") or 0)
    check("face-count.total_faces 与向量库条数一致（条数口径，非人数）",
          total_faces == vec_after,
          f"total_faces={total_faces} vs vector_count={vec_after}")
    check("total_faces 不等于有脸学生人数（防止再次把人数当条数）",
          total_faces != int(fc.get("one_face") or 0) + int(fc.get("multiple_faces") or 0)
          or total_faces == 0,
          f"total_faces={total_faces} vs one+multiple="
          f"{int(fc.get('one_face') or 0) + int(fc.get('multiple_faces') or 0)}")


def quality_gate_phase() -> None:
    """质量门控端到端验证：坏图必须被拒且不落库，好图必须放行。

    这是门控的核心业务价值 —— 脏数据进不来。
    验证方式：拿真实样本人工退化，走完整 HTTP 链路，
    断言「数据库里没多出这张脸照」。
    """
    print("\n[9] 质量门控（坏图拒收 + 不落库）")
    if not os.path.isdir(SAMPLE_DIR):
        check("质量门控用例（跳过：缺 scripts/sample_faces/）", True, "skipped")
        return
    files = sorted(f for f in os.listdir(SAMPLE_DIR) if f.lower().endswith(IMG_EXT))
    if not files:
        check("质量门控用例（跳过：目录为空）", True, "skipped")
        return

    enums = call("GET", "/api/enums").get("data") or {}
    created = call("POST", "/api/students", {
        "student_no": f"QG{random.randint(100000, 999999)}",
        "name": "门控测试",
        "gender": "男",
        "class_name": (enums.get("classes") or ["智造本2024-1班"])[0],
        "major": (enums.get("majors") or ["计算机科学与技术"])[0],
        "enroll_year": 2024,
    })
    sid = (created.get("data") or {}).get("id")
    if not sid:
        check("质量门控用例（建档失败）", False, str(created.get("message"))[:80])
        return

    # ---- 1) 预检接口：坏图应被拒 ----
    src = os.path.join(SAMPLE_DIR, files[0])
    with open(src, "rb") as fp:
        original = fp.read()
    img = cv2.imdecode(np.frombuffer(original, np.uint8), cv2.IMREAD_COLOR)

    # 用 σ=4 的中度模糊：检测器仍能认出人脸，但画质不达标 ——
    # 这才真正走到门控逻辑。σ=8 会让人脸直接检不出（no face detected），
    # 那是上游检测的拒绝，不是门控在起作用。
    blurred = cv2.imencode(".jpg", cv2.GaussianBlur(img, (0, 0), 4.0))[1].tobytes()
    b_body, b_hdrs = multipart("file", "blur.jpg", blurred)
    prev = call("POST", "/api/faces/preview", form=b_body, headers=b_hdrs)
    pdata = prev.get("data") or {}
    check("POST /api/faces/preview 模糊图被拒", prev.get("code") == 0 and pdata.get("passed") is False,
          (pdata.get("reason") or prev.get("message") or "")[:60])
    check("预检返回了触发规则", bool(pdata.get("rule")), str(pdata.get("rule")))

    # 预检不应产生任何副作用
    before = call("GET", f"/api/students/{sid}/faces").get("data") or []
    check("预检不落库（脸照数未变）", len(before) == 0, f"faces={len(before)}")

    # ---- 2) 模糊图走录入接口：必须被拒且不入库 ----
    body, hdrs = multipart("file", "blur.jpg", blurred)
    r = call("POST", f"/api/students/{sid}/faces", form=body, headers=hdrs)
    rdata = r.get("data") or {}
    check("模糊图录入被门控拒绝", r.get("code") == 0 and rdata.get("enrolled") is False,
          (rdata.get("reason") or r.get("message") or "")[:60])
    after = call("GET", f"/api/students/{sid}/faces").get("data") or []
    check("被拒的脏图未写入数据库", len(after) == 0, f"faces={len(after)}")

    # ---- 3) 过暗图：应报「光照不足」而不是「画面模糊」 ----
    dark = cv2.imencode(".jpg", (img.astype(np.float32) * 0.18).astype(np.uint8))[1].tobytes()
    d_body, d_hdrs = multipart("file", "dark.jpg", dark)
    dr = call("POST", "/api/faces/preview", form=d_body, headers=d_hdrs)
    ddata = dr.get("data") or {}
    check("过暗图被拒", ddata.get("passed") is False,
          (ddata.get("reason") or "")[:60])
    check("过暗提示指向光照（G6）而非模糊（G7）", ddata.get("rule") == "G6",
          f"rule={ddata.get('rule')} reason={ddata.get('reason')}")

    # ---- 4) 正常图：应放行并入库 ----
    ok_body, ok_hdrs = multipart("file", files[0], original)
    ok_r = call("POST", f"/api/students/{sid}/faces", form=ok_body, headers=ok_hdrs)
    okdata = ok_r.get("data") or {}
    check("正常图通过门控并入库", ok_r.get("code") == 0 and okdata.get("enrolled") is True,
          (okdata.get("reason") or ok_r.get("message") or "")[:60])
    final_faces = call("GET", f"/api/students/{sid}/faces").get("data") or []
    check("入库后脸照数为 1（只有合格那张）", len(final_faces) == 1, f"faces={len(final_faces)}")

    call("DELETE", f"/api/students/{sid}")


def main() -> None:
    print("=" * 66)
    print("学生刷脸考勤系统 · 第一阶段 端到端联调")
    print("=" * 66)

    print("\n[1] 健康检查 / 系统状态 / 枚举")
    check("GET /api/health", call("GET", "/api/health").get("code") == 0)
    status = call("GET", "/api/system/status")
    print("      系统状态:", json.dumps(status.get("data"), ensure_ascii=False))
    check("GET /api/system/status", status.get("code") == 0)
    enums = call("GET", "/api/enums").get("data") or {}
    check("GET /api/enums 有班级/专业", bool(enums.get("classes")) and bool(enums.get("majors")),
          f"classes={len(enums.get('classes', []))}, majors={len(enums.get('majors', []))}")

    print("\n[2] 学生 CRUD")
    no = f"E2E{random.randint(10000, 99999)}"
    created = call("POST", "/api/students", {
        "student_no": no, "name": "联调同学", "gender": "男",
        "class_name": (enums.get("classes") or ["智造本2024-1班"])[0],
        "major": (enums.get("majors") or ["计算机科学与技术"])[0],
        "enroll_year": 2024, "phone": "13800001111",
    })
    sid = (created.get("data") or {}).get("id")
    check("POST /api/students", created.get("code") == 0 and sid, created.get("message", ""))

    dup = call("POST", "/api/students", {"student_no": no, "name": "重复学号"})
    check("学号唯一性校验", dup.get("code") != 0, dup.get("message", ""))

    bad_enum = call("POST", "/api/students", {"student_no": no + "X", "name": "枚举校验", "class_name": "不存在的班级"})
    check("班级枚举校验", bad_enum.get("code") != 0, bad_enum.get("message", "")[:60])

    listed = call("GET", f"/api/students?keyword={no}&page=1&page_size=10")
    check("GET /api/students 搜索", listed.get("code") == 0 and listed["data"]["total"] >= 1)

    upd = call("PUT", f"/api/students/{sid}", {"phone": "13900002222"})
    check("PUT /api/students/{id}", upd.get("code") == 0, upd.get("message", ""))

    print("\n[3] 人脸录入（含无脸拒收）")
    body, hdrs = multipart("file", "face.jpg", synth_face(1234))
    enrolled = call("POST", f"/api/students/{sid}/faces", form=body, headers=hdrs)
    detected = (enrolled.get("data") or {}).get("detected") is True
    if detected:
        check("POST /api/students/{id}/faces 录入成功", enrolled.get("code") == 0,
              f"face_id={(enrolled.get('data') or {}).get('face_id')}")
    else:
        # 契约：未检出人脸时返回 code=0 + data.detected=False + reason，
        # 前端据此弹友好提示而不触发全局错误拦截（见 FaceEnroll.vue）
        check("人脸检测生效（合成图被拒收，属预期）",
              enrolled.get("code") == 0 and (enrolled.get("data") or {}).get("detected") is False,
              (enrolled.get("data") or {}).get("reason", enrolled.get("message", "")))

    blank = np.zeros((200, 200, 3), dtype=np.uint8)
    blank_body, blank_hdrs = multipart("file", "blank.jpg", cv2.imencode(".jpg", blank)[1].tobytes())
    rejected = call("POST", f"/api/students/{sid}/faces", form=blank_body, headers=blank_hdrs)
    check("无脸图片被拒收", (rejected.get("data") or {}).get("detected") is False or rejected.get("code") != 0,
          (rejected.get("data") or {}).get("reason", rejected.get("message", ""))[:60])

    faces = call("GET", f"/api/students/{sid}/faces")
    check("GET 脸照列表", faces.get("code") == 0)

    print("\n[4] 汇总统计")
    s = call("GET", "/api/stats/summary")
    check("GET /api/stats/summary", s.get("code") == 0 and "total_students" in (s.get("data") or {}),
          f"总人数={(s.get('data') or {}).get('total_students')}")
    cov = call("GET", "/api/stats/face-coverage").get("data") or {}
    check("GET /api/stats/face-coverage", "coverage_rate" in cov, f"覆盖率={cov.get('coverage_rate')}")
    fc = call("GET", "/api/stats/face-count").get("data") or {}
    check("GET /api/stats/face-count", "multiple_faces" in fc, json.dumps(fc, ensure_ascii=False))

    print("\n[5] 脸库审计（ML 聚类）")
    audit = call("GET", "/api/audit/duplicates")
    ad = audit.get("data") or {}
    check("GET /api/audit/duplicates", audit.get("code") == 0,
          f"backend={ad.get('backend')} 向量={ad.get('face_count')} 重复对={ad.get('summary', {}).get('duplicate_pairs')} "
          f"脏数据对={ad.get('summary', {}).get('dirty_pairs')} 集群={ad.get('summary', {}).get('cluster_count')}")

    print("\n[6] 公告 + WebSocket")
    asyncio.run(ws_test())
    notices = call("GET", "/api/notices?page=1&page_size=5")
    check("GET /api/notices", notices.get("code") == 0, f"共 {notices['data']['total']} 条" if notices.get("code") == 0 else "")

    print("\n[7] 级联删除")
    deleted = call("DELETE", f"/api/students/{sid}")
    dd = deleted.get("data") or {}
    check("DELETE /api/students/{id}", deleted.get("code") == 0,
          f"清理向量 {dd.get('deleted_vectors')} 条, 文件 {len(dd.get('deleted_files') or [])} 个")
    gone = call("GET", f"/api/students/{sid}")
    check("删除后查询返回不存在", gone.get("code") != 0, gone.get("message", ""))

    real_face_phase(enums)

    quality_gate_phase()

    print("\n" + "=" * 66)
    print(f"结果：通过 {len(passed)} 项，失败 {len(failed)} 项")
    if failed:
        print("失败项：")
        for f in failed:
            print("  -", f)
    print("=" * 66)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
