# 自主维护手册（不用找 AI 也能自己改）

> 面向：项目作者本人（FlowerJan10）
> 目标：这份文档写完后，**你可以自己完成**改代码、跑测试、提交、推送、点亮绿点、恢复误操作等全部日常操作。
> 前提：会用 Windows 的 PowerShell / CMD，浏览器能打开 Gitee。

---

## 目录

- [第 0 章 三十秒速查](#第-0-章-三十秒速查)
- [第 1 章 绿点为什么不亮 + 怎么点亮](#第-1-章-绿点为什么不亮--怎么点亮)
- [第 2 章 提交与推送的标准流程](#第-2-章-提交与推送的标准流程)
- [第 3 章 改代码改哪里](#第-3-章-改代码改哪里)
- [第 4 章 跑测试](#第-4-章-跑测试)
- [第 5 章 令牌管理](#第-5-章-令牌管理)
- [第 6 章 出错了怎么回退](#第-6-章-出错了怎么回退)
- [第 7 章 一键体检](#第-7-章-一键体检)
- [第 8 章 换新电脑 / 重新克隆](#第-8-章-换新电脑--重新克隆)
- [第 9 章 术语表](#第-9-章-术语表)

---

## 第 0 章 三十秒速查

**在仓库根目录**（`E:\workbuddy-projects\face-attendance`）打开 PowerShell：

| 你要做的事 | 命令 |
|---|---|
| 看现在什么状态 | `git status` |
| 看最近提交 | `git log --oneline -10` |
| 改完文件后暂存 | `git add .` |
| 提交 | `git commit -m "feat: 你这次改了什么"` |
| 推送 | `git push origin master` |
| 体检（身份/同步/敏感信息/绿点） | `python scripts/doctor.py` |
| 启动后端 | `scripts\start-backend.cmd` |
| 启动前端 | `scripts\start-frontend.cmd` |
| 跑全部测试 | `cd backend; .venv\Scripts\python.exe -m pytest test/ -q` |
| 跑端到端 | `cd backend; .venv\Scripts\python.exe scripts/e2e_test.py` |

**改代码的黄金三步**（每次都照做）：

```
1. 改代码
2. 跑测试（pytest + e2e）确认没坏
3. git add → git commit → git push
```

---

## 第 1 章 绿点为什么不亮 + 怎么点亮

### 1.1 先看现象

Gitee 个人主页 https://gitee.com/flowerjan10 上方的贡献方块（绿点），
**2026 年全年只有 1 天亮着**（10-16，2 个贡献）。

但实际上：

- 仓库里有 **30 条提交**（`git log --oneline | wc -l`）
- Gitee 贡献者接口显示这 30 条**全部归属到 FlowerJan10**（`name` 字段正确）
- 推送时间线有 **20 条 push 事件**，`is_proper=True`（都是正常推送，不是 fork）

也就是说：**提交在、归属对、推送成功，但日历不统计。**

### 1.2 根因

Gitee 贡献度的统计规则（官方帮助中心《代码贡献未被统计》）：

> 代码提交的次数需**本地配置的 git 邮箱是 Gitee 帐号已确认绑定的**才会被统计。

而你本地现在的配置是：

```bash
git config user.email
# flowerjan10@users.noreply.gitee.com
```

注意 `users` 是**复数**。这是 **GitHub** 的隐私邮箱写法（GitHub 的是 `users.noreply.github.com`）。

**Gitee 官方的是单数 `user`，而且带 `+用户名` 前缀**：

```
xxxx+flowerjan10@user.noreply.gitee.com
```

Gitee 从来没生成过 `flowerjan10@users.noreply.gitee.com` 这个地址，
你的账号「多邮箱管理」里也不可能绑定它 —— 所以这 30 条提交，
Gitee 认得出「这是谁」（因为 `user.name` 是 FlowerJan10），
但认不出「这是哪个邮箱」，于是**不计入贡献度**。

> 顺带说明：contributors 接口里 `2334169670@qq.com` 那 2 条显示归属正确，
> 但那是历史遗留（早期 2 个 commit），不代表它现在还在生效。

### 1.3 修复步骤

**第 1 步 —— 确认你的数字用户 ID**

打开 <https://gitee.com/flowerjan10>，从头像图片的 URL 里读出那串数字。
本账号 = `17713492`。

> ⚠️ 前缀数字写错的后果：提交会被签到那个 ID 对应的账号名下。
> 本项目就踩过 —— 用占位示例 `12345+...` 提交，网页署名显示成了别人。

**第 2 步 —— 改本地配置**

```bash
git config --global user.email "17713492+flowerjan10@user.noreply.gitee.com"
git config --global user.name  "FlowerJan10"
```

> `--global` 改的是你**所有**仓库的默认身份，以后换项目不用再改。
> 如果只想改这一个仓库，把 `--global` 去掉即可。

**第 3 步 —— 做一次新提交验证**

```bash
git commit --allow-empty -m "chore: 绿点归属验证"
git push origin master
```

**第 4 步 —— 校验归属**

用 1.2·1 里的命令确认 `author.id = 17713492`。

**第 5 步 —— 看结果**

等 1~5 分钟（Gitee 贡献度是异步统计的，不是实时），刷新 <https://gitee.com/flowerjan10>。

- ✅ **亮了** → 以后所有提交都会自动点亮，这事就结束了。
- ❌ **没亮** → 回第 1 步，确认那个地址在「多邮箱管理」里状态是**已验证**。
  只「添加」没「验证」是不算的。

> 📌 **历史绿点补不回来。** Gitee 官方明确说明「以前提交过的代码贡献度暂不支持回溯」。
> 邮箱配对之前的那 28 条提交，绿点是永久缺失的 —— 只能靠今后的新提交累积。
> 这也是「不改写历史」的技术依据：改写也补不回来，反而让已有记录错位。

**第 6 步 —— 清理这次验证提交（可选）**

绿点验证用的是一个无意义的提交。想抹掉它：

```bash
git reset --hard HEAD~1      # 撤销这条空提交（它不带任何文件改动，撤销无损失）
```

> ⚠️ **不要用 `git reset --hard`**、不要用 `git rebase` 改历史。
> 原因：Gitee 绿点按 commit 的**作者邮箱 + 作者日期**统计，
> 重写历史会让已有的贡献记录错位或消失，README「十·补」节里记的就是这件事。

**如果绿点亮了还想让它更密**：绿点按天算，一天提交 N 次当天也只是 1 格（但那天会显示更深的颜色）。
想连成一片，就**每天都提交一点**，哪怕只是修个错别字。

### 1.4 以后每次提交前，30 秒自检

```bash
git config user.email
```

输出应该长这样（单数 `user` + `+你的数字用户ID`）：

```
17713492+flowerjan10@user.noreply.gitee.com
```

两种错法都要立刻改：

- 看到 `users.noreply`（**复数**）→ 域名是 GitHub 的写法，Gitee 认不出
- 看到 `12345+...` 之类 → 数字不是你的 ID，提交会签到别人名下

改法见 1.3 第 2 步。

---

## 第 2 章 提交与推送的标准流程

### 2.1 完整命令序列

```bash
cd E:\workbuddy-projects\face-attendance

git status                 # ① 看哪些文件被改了
git diff                   # ② 逐行确认改动内容（重要！别跳过）

git add .                  # ③ 暂存所有改动
git status                 # ④ 再确认一次暂存了什么

git commit -m "feat: 简明描述这次改了什么"    # ⑤ 提交
git push origin master     # ⑥ 推送
```

### 2.2 commit message 怎么写

格式：`<类型>: <一句话描述>`

| 类型 | 用于 |
|---|---|
| `feat` | 新功能 |
| `fix` | 修 bug |
| `docs` | 只改文档 |
| `test` | 只改测试 |
| `refactor` | 重构，不改行为 |
| `chore` | 杂活（配置、依赖、脚本） |

**好例子**：
```
feat: 录入接口增加画质门控，脏数据挡在入库前
fix: 切换学生时画质诊断面板未清空
docs: 统一相似度区间为完整评测集数据
```

**坏例子**：
```
update
fix bug
改了点东西
```

### 2.3 一次改多个东西时

建议**分多次提交**，一个 commit 只干一件事：

```bash
git add backend/core/quality_gate.py
git commit -m "feat: 门控增加饱和度判据"

git add backend/test/test_quality_gate.py
git commit -m "test: 补饱和度判据的单元测试"

git add README.md
git commit -m "docs: 门控规则表补充饱和度说明"

git push origin master
```

面试时这种提交历史本身就是加分项 —— 能看出你做事有章法。

### 2.4 推送失败怎么办

| 报错 | 原因 | 解决 |
|---|---|---|
| `403 token username invalid` | 缓存了旧令牌 | 见第 5 章，重新生成令牌 |
| `! [rejected] ... (non-fast-forward)` | 远端有你没有的提交 | `git pull --rebase origin master` 然后再 push |
| `GE007: Your push would publish a private email address` | 提交里含你设为不公开的邮箱 | 改用 Gitee 的隐私提交邮箱（见 1.3） |
| `Please make sure you have the correct access rights` | 令牌没有 projects 写权限 | 重新生成时勾上 projects |
| `Permission denied (publickey)` | SSH 相关 | 本项目用 HTTPS 推送，不该遇到；检查 remote 地址 |

**最稳的推送方式**（避开缓存令牌问题）：

```bash
git -c credential.helper= push origin master
```

此时会提示输入 Username 和 Password：
- Username 填 `flowerjan10`（**不是邮箱**）
- Password 填**私人令牌**（不是登录密码）

### 2.5 不想在密码框里输令牌？存到文件

仓库根目录建一个 `.gitee-token` 文件（**已被 .gitignore 忽略，不会入库**）：

```
你的令牌字符串
```

然后：

```bash
TOKEN=$(tr -d '\r\n' < .gitee-token)
git push https://flowerjan10:$TOKEN@gitee.com/flowerjan10/face-attendance.git master
```

> ⚠️ 这样令牌会进 shell history。用完记得 `history -c`。
> 推完后**务必**确认 remote 没被改成带令牌的地址：
> ```bash
> git remote set-url origin https://gitee.com/flowerjan10/face-attendance.git
> git remote -v   # 确认没有令牌
> ```

---

## 第 3 章 改代码改哪里

### 3.1 目录地图

```
face-attendance/
├─ backend/                    后端（Python / FastAPI）
│  ├─ main.py                  应用入口、路由注册
│  ├─ core/                    核心层
│  │  ├─ config.py             配置（端口、路径、阈值）
│  │  ├─ db.py                 SQLAlchemy 引擎 / Session
│  │  ├─ milvus.py             向量库封装
│  │  ├─ face_engine.py        人脸检测 + 特征提取（insightface 封装）
│  │  ├─ face_types.py         FaceCandidate 数据类
│  │  ├─ quality_gate.py       ★ 画质门控（G1~G7 规则、阈值）
│  │  ├─ enums.py              枚举
│  │  ├─ exceptions.py         自定义异常
│  │  ├─ response.py           统一响应包装
│  │  └─ ws_manager.py         WebSocket 管理
│  ├─ models/                  ORM 实体（student / face / notice）
│  ├─ schemas/                 Pydantic 出入参
│  ├─ repository/              数据访问层（CRUD）
│  ├─ service/                 业务逻辑层 ★ 改业务主要在这
│  │  ├─ face_service.py       ★ 录入流水线（检测→门控→提取→存）
│  │  ├─ student_service.py    学生管理
│  │  ├─ cluster_service.py    层次聚类审计
│  │  ├─ stats_service.py      统计
│  │  └─ notice_service.py     通知
│  ├─ api/                     路由层（只做参数校验 + 调 service）
│  │  ├─ faces.py              ★ 人脸录入/查询/预检
│  │  ├─ students.py           学生
│  │  ├─ audit.py              聚类审计
│  │  ├─ stats.py              统计
│  │  ├─ notices.py            通知
│  │  └─ system.py             系统信息
│  ├─ scripts/                 可执行脚本（见 3.3）
│  └─ test/                    测试
│     ├─ test_quality_gate.py  ★ 门控单测（19 项）
│     └─ test_student_repo.py
│
├─ frontend/src/               前端（Vue 3）
│  ├─ views/                   页面
│  │  ├─ FaceEnroll.vue        ★ 人脸录入页（含画质诊断面板）
│  │  ├─ ...                    其它页面
│  ├─ components/
│  │  └─ CameraCapture.vue     ★ 摄像头抓拍组件
│  ├─ api/                     后端接口封装
│  │  ├─ face.js               ★ 人脸相关
│  │  ├─ student.js / audit.js / stats.js / notice.js
│  │  └─ request.js            axios 实例（baseURL 在这里）
│  └─ router/ store/
│
├─ scripts/                    运维脚本
│  ├─ doctor.py                ★★ 自助体检工具（先跑这个）
│  ├─ check-gitee-contrib.sh   绿点归属巡检
│  ├─ push-to-gitee-safe.bat   安全推送
│  ├─ start-backend.cmd        启动后端
│  └─ start-frontend.cmd       启动前端
│
├─ docs/                       文档
│  ├─ blog/                    技术博客
│  ├─ demo-quality/            门控演示图
│  └─ screenshots/
│
├─ tools/                      一次性代码生成器（不用日常碰）
├─ README.md                   ★ 项目主文档
├─ RESUME.md                   ★ 简历素材 + 面试问答
└─ 每日清单.md                  持续记录
```

### 3.2 分层规矩（改代码前先想清楚改哪层）

```
api (路由)  →  service (业务)  →  repository (数据)  →  db (引擎)
                                    ↑
                        core (人脸/向量/门控 等领域能力)
```

| 你要改什么 | 改哪个文件 |
|---|---|
| 加一个接口 | `backend/api/xxx.py` 加路由函数 → `backend/service/xxx.py` 加逻辑 |
| 改录入流程的判断顺序 | `backend/service/face_service.py` |
| 改画质门控的规则/阈值 | `backend/core/quality_gate.py` |
| 加一张数据库表 | `backend/models/` + `backend/repository/` |
| 改页面显示 | `frontend/src/views/` 对应 .vue |
| 改调后端的接口路径 | `frontend/src/api/` 对应 .js |
| 改提示文案 | `frontend/src/views/` 里直接找中文串 |

**规矩**：路由层不写业务逻辑，service 层不直接碰 SQL。
现有代码是这个分层，面试时也讲得出「分层单体」，改的时候别破坏它。

### 3.3 常用脚本

| 脚本 | 干什么 | 怎么用 |
|---|---|---|
| `scripts/doctor.py` | **体检**：身份/同步/敏感信息/绿点 | `python scripts/doctor.py` |
| `backend/scripts/e2e_test.py` | 端到端 68 项断言 | `cd backend; .venv\Scripts\python.exe scripts/e2e_test.py` |
| `backend/scripts/calibrate_quality.py` | 重新标定门控阈值 | `cd backend; .venv\Scripts\python.exe scripts/calibrate_quality.py` |
| `backend/scripts/seed_demo.py` | 重建演示数据库 | `cd backend; .venv\Scripts\python.exe scripts/seed_demo.py` |
| `backend/scripts/pii_guard.py` | 截图前查库，防止真实手机号出图 | `cd backend; .venv\Scripts\python.exe scripts/pii_guard.py` |
| `backend/scripts/capture_ui.py` | 生成界面截图（**必须先跑 pii_guard**） | `cd backend; .venv\Scripts\python.exe scripts/capture_ui.py` |
| `backend/scripts/audit_recall_test.py` | 聚类召回率评测 | `cd backend; .venv\Scripts\python.exe scripts/audit_recall_test.py` |
| `backend/scripts/download_model.py` | 下载 buffalo_l 模型（600MB） | 模型丢失时跑 |

### 3.4 改门控阈值的正确姿势

**不要直接改数字然后提交。** 阈值是实测标定出来的，改了要重新标定：

1. 改 `backend/core/quality_gate.py` 里的常量
2. 跑标定脚本 `calibrate_quality.py`，看输出
3. 看是否满足：**合格样本 0 误伤 + 退化样本 100% 拦截**
4. 同步更新 `backend/test/test_quality_gate.py` 的断言
5. 更新 README 的阈值标定表（数字要和代码一致，README 里写的是实测值）

---

## 第 4 章 跑测试

### 4.1 单元测试

```bash
cd E:\workbuddy-projects\face-attendance\backend
.venv\Scripts\python.exe -m pytest test/ -q
```

期望：`19 passed`

### 4.2 端到端测试

```bash
cd E:\workbuddy-projects\face-attendance\backend
.venv\Scripts\python.exe scripts/e2e_test.py
```

期望：`68/68 通过`

> 端到端需要后端能起（脚本自己会起服务）。
> 需要 insightface 模型在 `backend/models/` 下；没有就跑 `download_model.py`（600MB）。

### 4.3 前端构建

```bash
cd E:\workbuddy-projects\face-attendance\frontend
npm install       # 第一次或依赖变了
npm run build
```

### 4.4 手动验收

```bash
scripts\start-backend.cmd      # 一个窗口
scripts\start-frontend.cmd     # 另一个窗口
```

浏览器打开 <http://127.0.0.1:5173>。

> 本机设了 `HTTP_PROXY` 的话，测试本机服务要加：
> ```bash
> set NO_PROXY=127.0.0.1,localhost
> ```

### 4.5 改了阈值/核心逻辑后的检查清单

```
[ ] pytest 34 项全过
[ ] e2e 68 项全过
[ ] 门控标定脚本输出仍是「0 误伤 + 100% 拦截」
[ ] README 里的数字和代码一致
[ ] 没有新的真实邮箱/手机号进仓库
[ ] python scripts/doctor.py 通过
```

---

## 第 5 章 令牌管理

### 5.1 生成

<https://gitee.com/profile/personal_access_tokens>

- 备注随便写，如 `face-attendance 本地推送`
- 权限勾选 **projects**（读写）
- 生成后**只显示一次**，立刻复制

### 5.2 存放

放进仓库根目录的 `.gitee-token` 文件（单行，不要换行）：

```
你的令牌
```

这个文件**已经在 `.gitignore` 里**，不会被提交。`doctor.py` 会检查这一条。

### 5.3 失效了怎么办

令牌被撤销 / 过期 / 权限改了，push 就会 401 或 403。
**重新生成一个，替换掉文件里的内容即可**，不用改任何代码。

### 5.4 安全红线

| 做 | 不做 |
|---|---|
| 令牌只放 `.gitee-token`（已 gitignore） | ❌ 写进任何被 git 跟踪的文件 |
| 令牌不用了就到页面吊销 | ❌ 令牌发到聊天/邮件/截图里 |
| 推完检查 `git remote -v` 没有令牌 | ❌ 令牌照抄进 remote URL 后忘了还原 |
| 定期 `doctor.py` 扫一遍 | ❌ 假设"我写代码时没泄露" |

> 之前排查时发现：`.git/config` 里残留过带令牌的 remote URL，
> 是靠 `grep` 扫 `.git/config` 确认清零的。这个坑要记住。

---

## 第 6 章 出错了怎么回退

### 6.1 提交错了但没推

```bash
git reset --soft HEAD~1    # 撤销提交，文件改动还在暂存区
git reset HEAD~1          # 撤销提交，改动回到工作区（推荐）
git reset --hard HEAD~1   # 撤销提交，改动全丢（⚠️ 慎用）
```

### 6.2 推错了想撤回

```bash
git log --oneline -5                       # 找到要撤的 commit
git revert <commit-hash>                  # ✅ 推荐：生成一个反向提交，历史不变
git push origin master
```

> ❌ **不要用 `git push --force`**。
> 绿点按 commit 的作者邮箱 + 作者日期统计，改写历史会让贡献记录错位或消失。
> 你的仓库是公开的，force push 还会让别人拉取失败。

### 6.3 改坏了想回到某个稳定版本

```bash
git log --oneline              # 找到好的 commit hash
git diff <hash> -- <文件路径>    # 先看看差了什么
git checkout <hash> -- <文件路径>  # 只把这个文件恢复过去
```

或者整体回退（同样不改历史）：

```bash
git revert <hash>..HEAD        # 逐个反向提交
```

### 6.4 彻底乱了想重来

```bash
git fetch origin
git reset --hard origin/master   # 本地完全对齐远端
```

> 这会**丢弃所有未推送的本地改动**。先 `git stash` 或复制一份再执行。

### 6.5 误删了文件

```bash
git status                    # 看到 "deleted: xxx"
git checkout -- xxx            # 恢复
```

### 6.6 `git add` 加错了文件

```bash
git reset                     # 取消全部暂存（文件还在）
git reset <文件路径>            # 只取消某个文件
```

---

## 第 7 章 一键体检

**每次推送后，或者隔一段时间，跑一次：**

```bash
cd E:\workbuddy-projects\face-attendance
python scripts/doctor.py
```

它会检查 5 件事：

1. **git 身份** —— 姓名/邮箱配对不对、邮箱域名写错（绿点根因）
2. **远端同步** —— 有没有漏推的提交、待推提交用的什么邮箱
3. **敏感信息** —— 令牌是否被 ignore、文件里有没有令牌字面量、
   真实邮箱、真实手机号
4. **绿点实测** —— 直接调 Gitee 接口看贡献日历到底亮了几格
5. **修复指引** —— 有问题就打印出对应的手工操作步骤

它是**只读**的：不改文件、不提交、不推送、不动 git 配置。

---

## 第 8 章 换新电脑 / 重新克隆

### 8.1 克隆

```bash
git clone https://gitee.com/flowerjan10/face-attendance.git
cd face-attendance
```

### 8.2 后端环境

```bash
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python.exe scripts/download_model.py    # 600MB
```

### 8.3 前端环境

```bash
cd frontend
npm install
```

### 8.4 配置身份（**别忘了这一步，否则绿点不亮**）

```bash
git config --global user.email "<第1章查到的提交邮箱>"
git config --global user.name  "FlowerJan10"
```

### 8.5 跑一遍确认

```bash
python scripts/doctor.py
```

---

## 第 9 章 术语表

| 词 | 意思 |
|---|---|
| **commit** | 一次提交，一个快照。`git log` 看得到 |
| **push** | 把本地提交上传到远端（Gitee） |
| **pull** | 把远端更新拉到本地。`git pull --rebase` 更干净 |
| **暂存区 (stage)** | `git add` 之后、`git commit` 之前，暂存待提交内容 |
| **工作区** | 你正在编辑的文件 |
| **HEAD** | 当前所在的位置（分支尖端） |
| **origin** | 远端仓库的默认代号，就是 Gitee 那个地址 |
| **master** | 主分支名（这个项目用的） |
| **绿点 / 贡献度** | Gitee 个人主页的贡献方块，按 commit 的作者邮箱 + 作者日期统计 |
| **fork** | 把别人的仓库复制一份到自己名下。fork 来的提交**不计**贡献度 |
| **令牌 (Token)** | Gitee 的私人访问令牌，代替密码用于 API 和推送 |
| **.gitignore** | 列出不该进版本库的文件 |
| **force push** | 强行覆盖远端历史。⚠️ 会破坏绿点，别用 |

---

## 附：三条不要

1. **不要 `git push --force`** —— 会毁掉绿点和别人的克隆。
2. **不要在聊天/截图/文档里放令牌** —— 放 `.gitee-token`。
3. **不要直接改门控阈值** —— 改完必须重跑标定脚本，并同步更新 README 的数字。

---

*文档版本：2026-10-21　　配套工具：`scripts/doctor.py`*
