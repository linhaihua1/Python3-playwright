# CI/CD 接入指南 —— 让 Jenkins 自动跑冒烟测试

> 适用读者：负责搭建/维护流水线的同学
> 前置阅读：[使用文档](使用文档.md)（了解工程结构与运行方式）
> 本文档目标：**按步骤把本工程接进 Jenkins，实现"代码构建成功 → 自动冒烟 → 结果反馈到构建页面"**

---

## 目录

- [一、接入目标与整体设计](#一接入目标与整体设计)
- [二、为 CI 做了哪些改造](#二为-ci-做了哪些改造)
- [三、前置条件准备](#三前置条件准备)
- [四、快速接入（5 步）](#四快速接入5-步)
- [五、环境变量覆盖配置](#五环境变量覆盖配置)
- [六、Jenkins 凭据配置](#六jenkins-凭据配置)
- [七、Jenkinsfile 详解](#七jenkinsfile-详解)
- [八、触发方式](#八触发方式)
- [九、产物与报告](#九产物与报告)
- [十、容器化运行方案](#十容器化运行方案)
- [十一、退出码约定](#十一退出码约定)
- [十二、常见失败排查](#十二常见失败排查)

---

## 一、接入目标与整体设计

### 1.1 目标流程

```
开发者提交代码
      │
      ▼
┌──────────────────┐
│ 构建任务（编译/   │   构建成功
│ 打包/部署到测试环境）├────────────┐
└──────────────────┘                │ 触发（Build other projects /
                                    │       webhook / 上游 job 里调 build）
                                    ▼
                        ┌───────────────────────────┐
                        │ UI 自动化任务             │
                        │  ① 拉代码 + 建虚拟环境     │
                        │  ② 跑冒烟用例（无头 Chromium）│
                        │  ③ 出 HTML 报告 + JUnit XML│
                        │  ④ 归档 + 反馈构建状态     │
                        └───────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
            构建页面显示「测试结果」            失败时邮件通知
            （用例明细 + 通过率趋势）
```

### 1.2 三条核心设计原则

| 原则 | 落地方式 |
|---|---|
| **构建状态自动判定** | `run.py` 返回标准退出码（0/1/2），非 0 让 stage 自动变红；同时输出 JUnit XML 供 Jenkins 展示用例明细 |
| **敏感信息不落仓库** | 账号密码走 Jenkins 凭据注入成 `UI_*` 环境变量，覆盖 `config.ini`；`config.ini` 已加入 `.gitignore` |
| **构建后只跑安全用例** | 默认跑 `smoke` 子集，只覆盖"登录 → 进系统 → 进素材库"，**不产生任何数据副作用**，可放心每次构建后执行 |

---

## 二、为 CI 做了哪些改造

为支持流水线，工程做了以下改造（都已提交在代码里，无需再改）：

| # | 问题（接入前） | 改造方案 | 涉及文件 |
|---|---|---|---|
| 1 | Jenkins 节点无桌面，浏览器起不来 | 新增无头模式：`[ci] headless` + `UI_CI_HEADLESS`，无头下自动附加 `--no-sandbox --disable-gpu --disable-dev-shm-usage --hide-scrollbars` 启动参数，窗口尺寸由浏览器上下文的 `viewport` 固定 1920×1080 | `Common/basePage.py`、`config.ini` |
| 2 | 脚本永远返回 0，Jenkins 无法判定失败 | `runner.run()` 返回 `exit_code`（0 通过 / 1 用例失败 / 2 环境异常），`run.py` 末尾 `sys.exit()` | `Common/runner.py`、`run.py`、`entrance.py` |
| 3 | 只有 HTML 报告，Jenkins 无法展示用例明细与趋势 | 新增 JUnit XML 输出，**测试只跑一遍**即同时产出 HTML + XML（通过 `resultclass` 注入结果收集类） | `Common/junitReport.py`、`Common/HTMLTestReportCN.py` |
| 4 | 账号密码明文在 `config.ini`，无法入库 | `Config.get()` 支持 `UI_<段>_<键>` 环境变量覆盖，Jenkins 用凭据注入；新增 `.gitignore` 与 `config.ini.example` | `Common/config.py`、`.gitignore`、`config.ini.example` |
| 5 | 全量用例会真实上传/报送，不敢每次构建都跑 | 新增冒烟子集 `--suite smoke`，由 `[ci] smoke_cases` 配置，默认只跑前 3 条无副作用用例 | `Common/runner.py`、`config.ini` |
| 6 | 环境地址写死在配置里，无法按分支/环境切换 | 支持 `UI_ENVIRONMENT_URL` 环境变量与 `--url` 参数覆盖 | `Common/config.py`、`run.py` |
| 7 | 报告里看不出是哪次构建、哪个分支 | 新增 `Common/ci.py` 收集构建上下文，自动拼进报告描述与 JUnit 属性 | `Common/ci.py`、`Common/runner.py` |
| 8 | 失败时要在海量控制台日志里翻原因 | 新增失败摘要打印（失败用例一行摘要直接输出到控制台） | `Common/runner.py` |
| 9 | 需要用本机浏览器代替自带 Chromium | 保留 `[ci] chrome_binary` / `UI_CI_CHROME_BINARY`（迁移 Playwright 后语义是 `executable_path`，**留空即用自带 Chromium，多数场景不再需要配**） | `Common/basePage.py` |
| 10 | 无流水线定义与容器镜像 | 新增 `Jenkinsfile`、`Dockerfile`、`.dockerignore` | 根目录 |
| 11 | 框架代码改错了要等几分钟跑完 UI 用例才知道 | 新增 `tests/` 框架单元测试（439 项 / 2 秒，不开浏览器、无业务副作用），并在流水线里作为**第一个质量门禁** | `tests/`、`Jenkinsfile` |

---

## 三、前置条件准备

### 3.1 Jenkins 节点要求

| 项 | 要求 |
|---|---|
| Python | 3.9+（实测 3.11.9） |
| 浏览器 | **无需安装**：Playwright 自管浏览器（`python -m playwright install chromium` 一次性下载）；配 `edge` 时用系统已装的 Edge，`firefox` 需另装 |
| 内存 | ≥ 2 GB（无头 Chromium 单实例约 500 MB～1 GB） |
| 磁盘 | 建议 ≥ 5 GB（报告 + 截图会累积，已配 `buildDiscarder` 保留 30 次） |
| 网络 | 能访问被测系统；首次需联网执行 `python -m playwright install chromium` 下载浏览器（约 150 MB，一次性） |
| 系统 | Linux / Windows 均可，`Jenkinsfile` 已做双平台分支 |

### 3.2 需要安装的 Jenkins 插件

| 插件 | 用途 | 必需？ |
|---|---|---|
| **JUnit** | 解析 `junit-results.xml`，构建页面显示用例明细与通过率趋势 | ★ 必需（Jenkins 通常自带） |
| **HTML Publisher** | 在构建页面内嵌查看 HTML 测试报告 | 推荐 |
| **Timestamps** | 控制台每行带时间戳，排查超时很有用 | 推荐 |
| **Docker Pipeline** | 使用容器化 agent 时必需 | 可选 |
| Email Extension | 更强的邮件通知（`Jenkinsfile` 默认用内置 `mail`，需配 SMTP） | 可选 |

### 3.3 被测环境准备

| 项 | 说明 |
|---|---|
| 测试环境地址 | 强烈建议用**测试环境**。生产环境跑全量会真实上传视频、提交报送单 |
| 测试账号 | 有登录权限且能看到素材库的账号 |
| 上传文件 | 全量测试需要 `upload_file` 指向的文件存在于执行节点上（冒烟不需要） |

---

## 四、快速接入（5 步）

### Step 1：创建 Jenkins 任务

**方式 A（单任务，推荐先用这个）**
`新建任务` → `Pipeline` → 名称填 `ui-automation-smoke`
→ Pipeline 区块选 `Pipeline script from SCM` → SCM 选 `Git` → 填仓库地址
→ **脚本路径填 `Jenkinsfile`** → 保存

**方式 B（多分支流水线，适合多分支并行）**
`新建任务` → `多分支流水线` → 分支源选 Git 仓库 → 它会自动识别每个分支下的 `Jenkinsfile`

### Step 2：创建两个凭据

`管理 Jenkins` → `凭据` → `系统` → `全局凭据` → `添加凭据`

| 凭据 ID | 类型 | 内容 |
|---|---|---|
| `ui-test-login-username` | **Secret text** | 登录账号 |
| `ui-test-login-password` | **Secret text** | 登录密码 |

> 凭据 ID 的前缀与 `Jenkinsfile` 参数 `CREDENTIALS_ID` 对应。
> 换用其他前缀时，记得同步修改任务参数。
> 若暂时不想用凭据，可在任务参数里把 `USE_CREDENTIALS` 关掉，改用 `config.ini` 里的账号
> （**仅限测试环境**，生产账号请不要落盘）。

### Step 3：首次执行，只跑冒烟

点「Build with Parameters」，按下面填：

| 参数 | 建议值 | 说明 |
|---|---|---|
| `TEST_SUITE` | `smoke` | 冒烟，不写数据，安全 |
| `TARGET_URL` | 测试环境地址 | 留空则用仓库配置（指向生产，**不推荐**） |
| `APP_BUILD_NO` | 被测应用的构建号 | 会写进报告与测试结果，便于追溯 |
| `HEADLESS` | `true` | 保持开启 |
| `USE_CREDENTIALS` | `true` | 用凭据里的账号密码 |
| `SEND_MAIL` | `false` | 首次先关闭，通了再开 |
| `NOTIFY_MAIL` | 失败通知收件人 | 留空则不发通知 |

### Step 4：确认结果

构建完成后检查三处：

1. **构建页面左侧出现「测试结果」** → 说明 JUnit XML 被正确解析
2. **「UI 自动化测试报告」链接能打开 HTML 报告** → 说明归档与 HTML Publisher 正常
3. **构建产物区有 `Report/` `Logs/` `img/`** → 说明归档正常

### Step 5：配置"构建后自动触发冒烟"

在**构建任务**里加以下任一种：

**方式 1：Post-build Action（最简单）**
构建任务 → `增加构建后操作步骤` → `Build other projects`
→ 填 `ui-automation-smoke` → 勾选 `Trigger only if build is stable`

**方式 2：在构建任务的流水线里调 build 步骤（可传参）**

```groovy
stage('构建后触发冒烟测试') {
    steps {
        build job: 'ui-automation-smoke',
              parameters: [
                  string(name: 'TARGET_URL',    value: 'http://测试环境地址/sso-portal/#/?redirect=/launch'),
                  string(name: 'APP_BUILD_NO',  value: "${env.BUILD_NUMBER}"),
                  string(name: 'TEST_SUITE',    value: 'smoke')
              ],
              wait: false          // false = 不阻塞主构建，异步触发
    }
}
```

**方式 3：Webhook**
多分支流水线 + GitLab/GitHub 插件，推到仓库即自动触发（适合"每次提交都冒烟"）。

---

## 五、环境变量覆盖配置

这是 CI 接入的**核心机制**：任何 `config.ini` 配置项都能被同名环境变量覆盖。

**命名规则**：`UI_` + 段名 + `_` + 键名，全大写，非字母数字转下划线。

### 5.1 CI 上常用的环境变量

| 环境变量 | 覆盖的配置项 | 用途 | 示例值 |
|---|---|---|---|
| `UI_ENVIRONMENT_URL` | `[environment] url` | 指定被测地址 | `http://test-env/sso-portal/#/?redirect=/launch` |
| `UI_ENVIRONMENT_BROWSER` | `[environment] browser` | 浏览器类型 | `chrome` |
| `UI_TESTCASE_LOGIN_USERNAME` | `[testcase] login_username` | 登录账号（凭据注入） | — |
| `UI_TESTCASE_LOGIN_PASSWORD` | `[testcase] login_password` | 登录密码（凭据注入） | — |
| `UI_TESTCASE_UPLOAD_FILE` | `[testcase] upload_file` | 上传文件路径 | `/data/videos/demo.mp4` |
| `UI_CI_HEADLESS` | `[ci] headless` | 无头开关 | `true` |
| `UI_CI_ELEMENT_TIMEOUT` | `[ci] element_timeout` | 元素超时秒数 | `30` |
| `UI_CI_IMPLICIT_WAIT` | `[ci] implicit_wait` | 隐式等待秒数 | `10` |
| `UI_CI_SMOKE_CASES` | `[ci] smoke_cases` | 冒烟用例关键字 | `test_1login,test_2select_scxt` |
| `UI_CI_JUNIT_XML` | `[ci] junit_xml` | JUnit XML 输出路径 | `Report/junit-results.xml` |
| `UI_CI_EXTRA_CHROME_ARGS` | `[ci] extra_chrome_args` | 附加浏览器启动参数（仅 chrome/edge 生效） | `--disable-images` |
| `UI_CI_CHROME_BINARY` | `[ci] chrome_binary` | 浏览器可执行文件路径（Playwright 的 `executable_path`；**留空 = 自带 Chromium**） | `/usr/bin/chromium` |
| `UI_ENV_OVERRIDE` | — | 整体关闭覆盖机制 | `false` |

零硬编码改造后，以下**必填业务配置**同样可被环境变量注入（多系统/多环境流水线的关键）：

| 环境变量 | 覆盖的配置项 | 用途 | 示例值 |
|---|---|---|---|
| `UI_ASSERT_TITLE_LOGIN` / `UI_ASSERT_TITLE_WORKBENCH` / `UI_ASSERT_TITLE_MATERIAL` | `[assert] title_*` | 断言用的期望页面标题（**必填**，缺失即退出码 2） | `统一门户` |
| `UI_TESTCASE_QUERY_KEYWORD` / `UI_TESTCASE_PHONE` / `UI_TESTCASE_REPORTING` | `[testcase] query_keyword / phone / reporting` | 查询关键字与表单字段（**必填**） | `测试` |
| `UI_REPORT_TITLE` / `UI_REPORT_FILENAME` / `UI_REPORT_TESTER` / `UI_REPORT_DESCRIPTION` | `[report] *` | 报告元信息（**必填**） | `每日冒烟报告` |
| `UI_TIMING_*` | `[timing] *` | 用例各步骤等待秒数（技术参数，CI 慢时整体调大，如 `UI_TIMING_MENU_LOAD=5`） | `5` |
| `UI_TUNING_*` | `[tuning] *` | 视口、轮询间隔等调优参数（如 `UI_TUNING_VIEWPORT=1366x768`） | `1366x768` |
| `UI_LOCATORS_*` | `[locators] *` | 元素界面文案（按钮文字/菜单名），如 `UI_LOCATORS_BTN_LOGIN=登入` | `登录` |
| `UI_CASE_NAMES_*` | `[case_names] *` | 用例在报告里的显示名，如 `UI_CASE_NAMES_LOGIN=登入成功` | `登录成功` |
| `UI_RUN_ID` | — | 指定本次执行的 runId（产物隔离子目录名），CI 上可用构建号，归档路径可预测 | `build-1234` |

> 💡 注意**"必填缺失"与"用例失败"在流水线里要区别对待**：业务配置没配时框架直接
> ABORTED（退出码 2，用例根本没跑），Jenkins 应把它标为需人工介入的环境问题（见第十一章），
> 而不是当成测试失败去查业务。

### 5.1.1 一个不覆盖 config.ini 的变量：`UI_LOG_DIR`

上表的变量都是"覆盖某个配置项"。`UI_LOG_DIR` 不属于这一类 ——
它直接指定**日志根目录**，必须在 `config.ini` 之前生效（`Common/log.py` 被 import
的那一刻就会建日志文件），所以只能是环境变量、无法写成配置项。

| 环境变量 | 作用 | 默认 |
|---|---|---|
| `UI_LOG_DIR` | 日志根目录 | `<工程根>/Logs` |

CI 上的典型用法：把日志收进构建产物目录，方便归档与下载

```groovy
environment {
    UI_LOG_DIR = "${env.WORKSPACE}/logs"
}
// 归档时连同日志一起收走
post 中的 archiveArtifacts 增加：'logs/**/*.log'
```

### 5.2 手工验证覆盖是否生效

```bash
# Windows
set UI_ENVIRONMENT_URL=http://my-test-env/portal
set UI_CI_HEADLESS=true
python run.py --suite smoke

# Linux / macOS
export UI_ENVIRONMENT_URL=http://my-test-env/portal
export UI_CI_HEADLESS=true
python run.py --suite smoke
```

日志里会出现 `配置 [environment] url 被环境变量 UI_ENVIRONMENT_URL 覆盖`。

> 值传**空字符串**时视为未设置，不会覆盖 —— 避免 Jenkins 传了空参数就把配置清空。

### 5.3 结果推送到群机器人（钉钉 / 企业微信）

流水线跑完，把结果（通过/失败、用例数、耗时、报告路径）自动推送到群，不用登录 Jenkins 也能第一时间知道。

```bash
python run.py --suite smoke --notify
```

配置（环境变量注入，CI 上更安全）：

| 变量 | 说明 |
|---|---|
| `UI_NOTIFY_WEBHOOK` | 机器人 webhook 地址（钉钉/企业微信），必填 |
| `UI_NOTIFY_SECRET` | 钉钉机器人「加签」密钥，未开启加签可不设 |
| `UI_NOTIFY_TITLE` | 推送消息标题前缀 |

> 推送失败只记日志、**不影响测试结果与退出码**，与邮件通知一致。建议与 `--notify` 搭配 `always` 后置步骤，确保失败也能收到告警。

---

## 六、Jenkins 凭据配置

### 6.1 为什么用凭据

- `config.ini` 含明文账号密码，已加入 `.gitignore`，**不入库**
- Jenkins 凭据的存储是加密的，且在**控制台输出里会自动脱敏**（密码不会被打到日志里）
- 同一个任务可以按环境挂不同的凭据（测试环境 / 预发环境）

### 6.2 `Jenkinsfile` 里的用法

```groovy
withCredentials([
    string(credentialsId: "${params.CREDENTIALS_ID}-username",
           variable: 'UI_TESTCASE_LOGIN_USERNAME'),
    string(credentialsId: "${params.CREDENTIALS_ID}-password",
           variable: 'UI_TESTCASE_LOGIN_PASSWORD')
]) {
    exec(cmd)
}
```

变量名恰好符合工程的环境变量命名规则，所以 `Common/config.py` 会自动用它覆盖
`config.ini` 的 `[testcase] login_username` / `login_password`，**代码零改动**。

### 6.3 换成其他存储

| 方案 | 做法 |
|---|---|
| Jenkins Credentials Binding 插件 | 把 `string(...)` 换成 `usernamePassword(credentialsId: 'xxx', usernameVariable: 'UI_TESTCASE_LOGIN_USERNAME', passwordVariable: 'UI_TESTCASE_LOGIN_PASSWORD')` |
| HashiCorp Vault | 用 Vault 插件读取后 `env.UI_TESTCASE_LOGIN_PASSWORD = value` |
| Kubernetes Secret | 用 k8s agent 时通过 `envFrom` 注入 Secret |

---

## 七、Jenkinsfile 详解

`Jenkinsfile` 是声明式流水线，共三个阶段 + 收尾处理。逐段说明：

### 7.1 `options` —— 稳定性与资源控制

| 配置 | 作用 | 为什么需要 |
|---|---|---|
| `timeout(time: 40, unit: 'MINUTES')` | 整体超时 40 分钟 | UI 测试卡死时不会一直占住执行器 |
| `timestamps()` | 控制台每行带时间戳 | 排查"卡在哪一步" |
| `buildDiscarder(numToKeepStr: '30')` | 只保留最近 30 次构建 | 报告与截图会累积，防止撑满磁盘 |
| `disableConcurrentBuilds()` | 禁止并行执行 | **关键**：UI 用例会操作同一套环境数据，两个构建同时跑必然互相干扰 |

### 7.2 `parameters` —— 运行时可调

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `TEST_SUITE` | choice | `smoke` | `smoke` / `full` |
| `TARGET_URL` | string | 空 | 被测地址，留空用仓库配置 |
| `APP_BUILD_NO` | string | 空 | 被测应用构建号/版本 |
| `HEADLESS` | boolean | `true` | 无头模式 |
| `SEND_MAIL` | boolean | `false` | 用工程内置邮件发报告 |
| `NOTIFY_MAIL` | string | 空 | 失败通知收件人 |
| `USE_CREDENTIALS` | boolean | `true` | 是否从凭据取账号密码 |
| `CREDENTIALS_ID` | string | `ui-test-login` | 凭据 ID 前缀 |

### 7.3 `environment` —— 流水线固定环境变量

```groovy
PYTHONIOENCODING = 'utf-8'      // 不加这句 Windows 节点中文日志会乱码
PYTHONUNBUFFERED = '1'          // 日志实时输出
UI_CI_HEADLESS   = "${params.HEADLESS}"   // ★ 无头开关
UI_CI_JUNIT_XML  = 'Report/junit-results.xml'
APP_BUILD_NO     = "${params.APP_BUILD_NO}"
```

### 7.4 四个阶段

| 阶段 | 做什么 | 关键点 |
|---|---|---|
| **拉取代码** | `checkout scm` 并打印分支/提交号 | 便于在控制台确认测的是哪份代码 |
| **准备运行环境** | 建 `.venv` 独立虚拟环境 + `pip install -r requirements.txt` + `python -m playwright install chromium` | 用独立 venv，避免污染节点全局 Python；浏览器由 Playwright 自管（首构建约 150 MB，之后有缓存）；`isUnix()` 分支处理 Windows/Linux 差异 |
| **框架单元测试** | `python tests/run_unit_tests.py --junit Report/unit-tests.xml` | **质量门禁**：不开浏览器、几秒、零业务副作用，先把"框架自身坏了"这类问题拦下来 |
| **执行冒烟测试** | 拼装命令并以 `sh`/`bat` 执行 | **sh/bat 返回非 0 会让本 stage 自动变红**，这就是"自动判定构建失败"的实现 |

> **为什么把单元测试放在 UI 测试之前**：UI 用例失败时，最常见的心智负担是
> "到底是框架坏了，还是页面改版了？"。先跑几秒的单元测试，就能把这两类原因分开 ——
> 单元测试红了先修框架；单元测试绿而 UI 红了，直接去看页面与环境。

实际执行的命令形如：

```bash
# ③ 框架单元测试（快，先跑）
<workspace>/.venv/bin/python tests/run_unit_tests.py --junit Report/unit-tests.xml

# ④ UI 冒烟测试
<workspace>/.venv/bin/python run.py --suite smoke --headless \
    --junit Report/junit-results.xml \
    --url http://测试环境/... --tester app-build-123
```

### 7.5 `post` —— 收尾（无论成败都执行）

| 块 | 内容 |
|---|---|
| `always` | `junit` 解析两份结果（`Report/junit-results.xml` + `Report/unit-tests.xml`） → `archiveArtifacts` 归档 `Report/ Logs/ img/` → `publishHTML` 内嵌报告 |
| `failure` | 按 `NOTIFY_MAIL` 发失败邮件（含构建链接与排查顺序提示） |
| `success` | 打印成功信息 |
| `aborted` | 提示构建被中止（超时或手动取消） |

> 归档必须放在 `post` 里 —— 放在 `stages` 中的话，测试一失败后续阶段就被跳过，最需要的失败产物反而归档不到。

### 7.6 关于 HTML Publisher 的 CSP 限制

Jenkins 默认的 CSP 策略会屏蔽报告里的内联 CSS/JS，导致 HTML 报告打开后**样式错乱**。
解决办法（任选）：

```groovy
// 方法一：在 Jenkins 脚本命令行（Manage Jenkins → Script Console）里执行
System.setProperty("hudson.model.DirectoryBrowserSupport.CSP", "")
```

```
// 方法二：在 Jenkins 启动参数里固化（jenkins.xml 的 arguments 或 JENKINS_JAVA_OPTIONS）
-Dhudson.model.DirectoryBrowserSupport.CSP=
```

```
// 方法三：用 init.groovy.d 脚本在启动时设置（推荐，重启不失效）
```

---

## 八、触发方式

| 方式 | 适用场景 | 配置位置 |
|---|---|---|
| **上游构建触发** | "代码构建成功后自动冒烟" —— **最贴合本工程需求** | 构建任务的 Post-build Actions 或 `build job:` 步骤 |
| **Webhook** | 每次提交即冒烟（Shift-Left） | 多分支流水线 + 代码仓库 Webhook |
| **定时回归** | 每天固定时间在测试环境跑全量 | `Jenkinsfile` 里取消注释 `triggers { cron(...) }` |
| **手动参数化** | 发布前验证、排查问题 | 「Build with Parameters」 |

### 8.1 推荐的组合策略

| 触发点 | 测试集 | 环境 | 目的 |
|---|---|---|---|
| 每次代码提交 | `smoke` | 测试环境 | 快速发现"页面都打不开"这类致命问题 |
| 每次构建成功 | `smoke` | 该构建部署的环境 | 验证部署结果可用 |
| 每晚定时 | `full` | 测试环境 | 全链路回归（含上传、报送） |
| 发布前手工 | `full` | 预发 / 生产（谨慎） | 上线前最终验证 |

### 8.2 定时回归的启用方法

打开 `Jenkinsfile`，取消这段注释：

```groovy
triggers {
    cron('H 8 * * 1-5')   // 工作日每天 8:30 左右跑一次
}
```

同时把 `TEST_SUITE` 的默认值改为 `full`，或新建一个独立的 nightly 任务指向同一仓库。

---

## 九、产物与报告

### 9.1 流水线产出的五类文件

| 产物 | 路径 | 归档方式 | 用途 |
|---|---|---|---|
| 框架单元测试结果 | `Report/unit-tests.xml` | `junit` 步骤解析 | 框架层问题的定位（配置、基类、执行器、报告） |
| UI 测试结果 | `Report/junit-results.xml` | `junit` 步骤解析 | 构建页面的「测试结果」、通过率趋势图 |
| HTML 报告 | `Report/*.html` | `archiveArtifacts` + `publishHTML` | 给人看的详细报告 |
| 运行日志 | `Logs/日期/All_Logs`、`Error_Logs` | `archiveArtifacts` | 排查失败根因（含模块名与行号） |
| 失败截图 | `img/日期/*.png` | `archiveArtifacts` | 断言失败瞬间的页面状态 |

### 9.2 构建页面上能看到什么

1. **测试结果**：每条用例的名称、耗时、失败堆栈；历史通过率曲线
2. **UI 自动化测试报告**：内嵌的 HTML 报告（需 HTML Publisher + CSP 设置）
3. **构建产物**：可直接下载 `Report/`、`Logs/`、`img/`
4. **控制台输出**：包含「失败摘要」区块，一眼看到挂了哪条、为什么

### 9.3 构建稳定性的使用建议

| 状态 | 含义 | 处理 |
|---|---|---|
| 成功 | 全部用例通过 | — |
| 失败 | 有用例未通过 | 看「测试结果」→ 定位用例 → 看归档截图与日志 |
| 超时/中止 | 环境无响应 | 检查被测系统是否可用、浏览器（Chromium）是否起得来 |

---

## 十、容器化运行方案

当 Jenkins 使用 Kubernetes / Docker agent，或节点上不方便装浏览器时，用容器方式（Playwright 镜像自带 Chromium，见下）。

### 10.1 构建镜像

```bash
docker build -t ui-auto-test:latest .
```

镜像基于 `python:3.11-slim`，已包含：

| 包 | 作用 |
|---|---|
| Playwright 自带 Chromium | `python -m playwright install --with-deps chromium` 一并装齐浏览器与全部系统运行库，**不再 apt 装 chromium / chromium-driver** |
| `fonts-noto-cjk` / `fonts-wqy-zenhei` | **中文字体**（缺字体时页面文字渲染成方块，会导致按文本定位失效、截图失去价值） |
| `tzdata` | 保证日志时间与本地一致 |

镜像内已设 `UI_CI_HEADLESS=true` 与 `PLAYWRIGHT_BROWSERS_PATH=/opt/playwright`（浏览器随镜像分发，运行期不再联网下载）；`[ci] chrome_binary` 留空即可，不再需要指向 `/usr/bin/chromium`。

### 10.2 本地验证

```bash
docker run --rm \
  -e UI_ENVIRONMENT_URL=http://测试环境/sso-portal/#/?redirect=/launch \
  -e UI_TESTCASE_LOGIN_USERNAME=你的账号 \
  -e UI_TESTCASE_LOGIN_PASSWORD=你的密码 \
  -v "$PWD/Report:/app/Report" \
  -v "$PWD/Logs:/app/Logs" \
  ui-auto-test:latest
```

### 10.3 在 Jenkinsfile 里切换成容器 agent

把顶层 `agent any` 换成：

```groovy
agent {
    docker {
        image 'ui-auto-test:latest'
        // /dev/shm 太小会导致 Chromium 崩溃，2g 是实践中的安全值
        args '--shm-size=2g'
        reuseNode true
    }
}
```

### 10.4 Kubernetes agent 片段

```groovy
agent {
    kubernetes {
        yaml '''
apiVersion: v1
kind: Pod
spec:
  containers:
    - name: ui-test
      image: ui-auto-test:latest
      command: ['cat']
      tty: true
      resources:
        requests: { memory: "1Gi", cpu: "500m" }
        limits:   { memory: "2Gi", cpu: "2" }
'''
    }
}
```

> 注意：容器方式下 `Logs/` `img/` `Report/` 都会产生在容器内，
> 需要像上面那样用 volume 挂出来，或依赖 `archiveArtifacts` 从工作目录归档。

---

## 十一、退出码约定

| 退出码 | 含义 | 触发条件 |
|---|---|---|
| `0` | 全部通过 | 用例失败数与错误数均为 0 |
| `1` | 用例失败 | 有用例断言失败或抛异常 |
| `2` | 环境/配置异常 | 未收集到任何用例；被测地址未配置；浏览器启动失败；**必填业务配置缺失**（日志含 `缺少必需配置：config.ini 的 [段] 键`，runner 会把这类错误从"用例出错"改判为环境异常） |

**在流水线里区分对待**（可选增强）：

```groovy
script {
    def rc = 0
    if (isUnix()) { rc = sh(script: cmd, returnStatus: true) } else { rc = bat(script: cmd, returnStatus: true) }
    if (rc == 2) {
        unstable('环境异常，需要人工介入（退出码 2）')
    } else if (rc != 0) {
        error("测试未通过（退出码 ${rc}）")
    }
}
```

> 默认的 `Jenkinsfile` 不做这个区分 —— 直接用 `sh cmd`，非 0 即失败，
> 这样能保证"有问题就一定红"。需要区分"环境问题"与"用例问题"时再按上面的写法改造。

---

## 十二、常见失败排查

### 12.1 按流水线阶段定位

| 失败阶段 | 常见原因 | 排查动作 |
|---|---|---|
| 准备运行环境 | 节点没装 Python；`pip install` 拉不到包（网络/代理）；`playwright install` 拉不到浏览器（内网需配 `PLAYWRIGHT_DOWNLOAD_HOST` 内网源，或预烘镜像） | 在节点上手工执行 `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python -m playwright install chromium` |
| 框架单元测试 | 框架代码被改坏；历史遗留缺陷回归 | 本地跑 `python tests/run_unit_tests.py` 复现。它不开浏览器，可在任意机器上验证；失败信息直接指出是哪个模块的哪条契约被破坏 |
| 执行冒烟测试 → 立刻失败，退出码 2 | 未收集到用例；地址未配置；**必填业务配置缺失**（日志有 `缺少必需配置：config.ini 的 [段] 键`） | 见下方「收集不到用例」；确认「未配置被测地址」；缺配置就按报错提示补 config.ini 或用 Credentials/环境变量注入对应 `UI_段_键` |
| 执行冒烟测试 → 浏览器启动失败 | 无头未开；没执行 `python -m playwright install chromium`；容器 `/dev/shm` 太小 | 确认 `UI_CI_HEADLESS=true`；跑一次 `playwright install chromium`；容器加 `--shm-size=2g` |
| 执行冒烟测试 → 元素大量定位不到 | 被测系统不可用；页面改版；切换标签页失败 | 看归档截图 `img/`；确认被测环境是否正常 |
| 归档结果 | 磁盘满；XML 未生成 | 检查 `Report/junit-results.xml` 是否存在；清理旧构建 |

> **判断技巧**：若「框架单元测试」绿、而 UI 测试红，基本可以直接排除框架问题，
> 去看页面改版或环境；若「框架单元测试」红，先修框架，UI 结果不必看。

**收集不到用例（本地正常、流水线为 0）—— 最典型的三个原因**：

1. **用例文件名大小写**：Windows 的文件名匹配不区分大小写、Linux 区分。
   `TestLogin.py` 在本机可被 `test*.py` 命中，在 Linux 节点上收不到。
   → 日志关键字：`大小写隐患`
2. **用例的文件名/方法名不合规**：`test-login.py`、`test login.py`、`测试登录.py` 会被
   discover **静默跳过**（不报错）；方法名不以 `test` 开头同样不收集。
   → 日志关键字：`文件名非法`
3. **`__init__.py` 缺失**：`TestSuites/` 或子目录缺它是硬性失败；若 `__init__.py` 被
   `.gitignore` 误排除、或从 Windows 带过来的工作区被清理掉，流水线就会重现。
   → 日志关键字：`包标识文件`、`not importable`

> 依赖没装（如节点缺 `playwright` 包）不会报"收集不到"，而是表现为**只有 1 条用例且报错**，
> 日志关键字 `用例模块导入失败`。这种全为占位对象的情况会直接判为环境异常（退出码 `2`）。
> 完整四类根因对照表见 [使用文档 7.1](使用文档.md#71-一条用例都没收集到深度排查)。

### 12.2 高频问题速查

| 现象 | 原因 | 解决 |
|---|---|---|
| 构建页面没有「测试结果」 | JUnit XML 没生成 | 确认 `--junit` 参数与 `[ci] junit_xml` 配置；确认 `junit testResults:` 路径一致 |
| 报告中文乱码 | 控制台/文件编码 | 设 `PYTHONIOENCODING=utf-8`（Jenkinsfile 已设） |
| HTML 报告样式错乱 | Jenkins CSP 限制 | 见 [7.6 章节](#76-关于-html-publisher-的-csp-限制) |
| 凭据读取报错 `Could not find credentials` | 凭据 ID 不匹配 | 确认创建的凭据 ID 是 `<CREDENTIALS_ID>-username` / `-password` |
| 日志里出现明文密码 | 用了 `echo` 打印环境变量 | Jenkins 会自动脱敏凭据变量；若自己 `echo` 拼接需谨慎 |
| 两个构建互相干扰 | 忘了 `disableConcurrentBuilds` | 已在 `Jenkinsfile` 的 `options` 里默认开启 |
| 每次都要重新装依赖，很慢 | venv 未被缓存 | 可加 `stash`/缓存目录，或在节点上预置固定路径的 venv |
| 冒烟集莫名其妙少跑用例 | 改了用例方法名，`smoke_cases` 关键字失配 | 改方法名时同步更新 `[ci] smoke_cases`；日志会打印「命中 N/M 条」 |
| 冒烟模式直接中止，退出码 2 | `[ci] smoke_cases` 被清空 | 这是**刻意的安全保护**：冒烟集为空时如果继续执行，就等于跑了全量、会真实写入生产数据，所以工程选择直接中止。补回 `smoke_cases` 即可 |

### 12.3 本地复现流水线行为（强烈推荐）

流水线失败时，先在**同一台机器**上用同样的参数复现：

```bash
# 1. 设置与流水线一致的环境变量
export PYTHONIOENCODING=utf-8
export UI_CI_HEADLESS=true

# 2. 用同样的命令
python run.py --suite smoke --headless \
    --junit Report/junit-results.xml \
    --url http://测试环境/...

# 3. 看退出码（Linux/Mac）
echo $?      # 应为 0
```

能本地复现的问题，就不用在 Jenkins 上反复试触发。

---

## 附录 A：CI 相关文件清单

| 文件 | 作用 |
|---|---|
| `Jenkinsfile` | Jenkins 声明式流水线定义 |
| `Dockerfile` | 容器化运行环境（Chromium + 中文字体） |
| `.dockerignore` | 镜像构建排除规则（排除 `config.ini` 等敏感文件） |
| `.gitignore` | Git 忽略规则（排除 `config.ini`、`Report/`、`Logs/`、`img/`） |
| `config.ini.example` | 配置模板（入库存档） |
| `Common/junitReport.py` | JUnit XML 结果输出 |
| `Common/ci.py` | CI 环境识别与构建元信息 |
| `Common/config.py` | 环境变量覆盖机制的实现 |
| `tests/run_unit_tests.py` | 框架单元测试入口（支持 `--junit` 输出，作为质量门禁） |

## 附录 B：接入检查清单

上线前逐项确认：

- [ ] 节点已装 Python 3.9+（无需装浏览器，Playwright 自管 Chromium；首次联网执行 `python -m playwright install chromium`，或用根目录 Dockerfile 的镜像）
- [ ] 已安装 JUnit、HTML Publisher 插件
- [ ] 本地 `python tests/run_unit_tests.py` 已全绿（框架层质量门禁先过）
- [ ] Jenkins 任务使用 `Pipeline script from SCM`，脚本路径为 `Jenkinsfile`
- [ ] 已创建 `ui-test-login-username` / `ui-test-login-password` 两个 Secret text 凭据
- [ ] `TARGET_URL` 指向**测试环境**而非生产环境
- [ ] 首次执行使用 `TEST_SUITE=smoke` 验证链路
- [ ] 构建页面能看到「测试结果」与 HTML 报告链接
- [ ] 已配置上游构建任务触发（Post-build Action 或 `build job:`）
- [ ] 失败通知收件人已配置（如需要）
- [ ] `buildDiscarder` 保留策略符合磁盘容量规划
