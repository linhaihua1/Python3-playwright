# Python3-Playwright 融媒体生产系统 UI 自动化

基于 **unittest + Playwright + PageObject** 的 Web UI 自动化测试工程，支持本机执行与
**Jenkins CI/CD 流水线自动冒烟**。

一次执行产出两份报告：**中文 HTML 报告**（给人看）+ **JUnit XML**（给流水线看）。

---

## 文档索引

| 文档 | 内容 | 什么时候看 |
|---|---|---|
| 本文 README | 快速上手、目录结构、常用命令 | **先看这个** |
| [docs/新手入门指南.md](docs/新手入门指南.md) | 零基础入门：建立认知、阅读顺序、跑起来、接 Jenkins | **第一次接触本工程** |
| [docs/实战-写你的第一条用例.md](docs/实战-写你的第一条用例.md) | 一次真实实操的全过程：想测什么 → 加元素 → 加动作 → 加用例 → 验证 | **想动手加第一条用例** |
| [docs/用例编写模板.md](docs/用例编写模板.md) | 可照抄的代码模板（串行业务流 / 独立用例 / 页面对象） | 写用例时随手翻 |
| [docs/使用文档.md](docs/使用文档.md) | 完整代码逻辑梳理、模块详解、扩展指南、排错、单元测试 | 要改用例 / 排查问题 |
| [docs/接入新系统指南.md](docs/接入新系统指南.md) | 把框架套到党政 / 财务 / ERP / 商城 / 数据中台等系统 | 要接入别的系统 |
| [docs/CI-CD接入指南.md](docs/CI-CD接入指南.md) | Jenkins 接入步骤、环境变量、凭据、容器化 | 要接流水线 |

---

## 一、快速开始

### 1.1 本机运行

```bash
# 1. 安装依赖（Playwright 自带浏览器与驱动管理，无需另装任何浏览器驱动）
pip install -r requirements.txt

# 2. 一次性下载 Playwright 自带浏览器（默认用 chromium，装完即可跑）
#    Windows/Mac： python -m playwright install chromium
#    Linux 新节点若缺系统运行库，可一并装齐： python -m playwright install --with-deps chromium
python -m playwright install chromium

# 3. 准备配置：复制模板后按注释修改
#    Windows:     copy config.ini.example config.ini
#    Linux/Mac:   cp config.ini.example config.ini
#    ⚠️ config.ini 含明文账号密码，已被 .gitignore 排除，不会入库
#    模板里标了【必填】的项（地址、账号、断言标题、报告元信息等）都要填 ——
#    框架零硬编码，缺必填配置会直接报错并告诉你缺哪个键（退出码 2），不会蒙跑

# 4. 先跑冒烟（只覆盖登录链路，不产生数据副作用）
python run.py --suite smoke

# 5. 环境通了再跑全量（⚠️ 会上传视频、提交报送单）
python run.py
```

跑完看四处产出：

| 产出 | 位置 | 用途 |
|---|---|---|
| HTML 报告 | `Report/[report] filename` 配置的文件（.html） | 浏览器打开，给人看 |
| JUnit XML | `Report/junit-results.xml` | 给流水线看 |
| 日志 | `Logs/日期/Error_Logs/xxx.log` | 失败原因，带模块名与行号 |
| 失败截图 | `img/日期/xxx.png` | 断言失败瞬间的页面 |

### 1.2 流水线运行

```bash
python run.py --suite smoke --headless --junit Report/junit-results.xml
```

退出码：`0` 全部通过 / `1` 有用例失败 / `2` 环境或配置问题（含**必填配置缺失**，报错会指明缺哪个段哪个键）。
Jenkins 直接用它判定构建成败。详见 [CI-CD 接入指南](docs/CI-CD接入指南.md)。

---

## 二、代码阅读顺序

按这个顺序读，每个文件都花不了几分钟：

| 顺序 | 文件 | 读它是为了搞清什么 |
|---|---|---|
| 0 | `config.ini.example` | 有哪些可配的东西 |
| 1 | `run.py` | 程序从哪进、参数怎么生效 |
| 2 | `Common/runner.py` | 测试怎么被收集、执行、出报告，退出码怎么来 |
| 3 | `TestSuites/testLogin.py` | **在测什么业务**（最重要的一个文件） |
| 4 | `PageObject/loginPage.py` | 每个业务动作怎么操作页面 |
| 5 | `Common/elementPage.py` | 元素定位值放在哪、哪个最脆弱 |
| 6 | `Common/basePage.py` | 浏览器操作怎么封装（唯一直接调 Playwright 的文件） |
| 7 | `Common/config.py` | 配置怎么读、环境变量怎么覆盖 |
| 8 | `Common/log.py` | 日志写去哪、怎么看 |

**对接流水线时再补这两个**：`Common/ci.py`（构建上下文）、`Common/junitReport.py`（JUnit 结果）。

其余文件（`dir_config.py` / `sentMail.py` / `configOverWriter.py` / `entrance.py` / 两个手工脚本）按需再看。

---

## 三、被测业务是什么

```
统一门户(登录)  →  融媒体生产系统(工作台)  →  素材库  →  上传 / 查询 / 报送
 title:统一门户     title:工作台-云媒资系统     title:素材库-云媒资系统
```

6 条用例串行走完这条链：

| 用例 | 做什么 | 断言 | 副作用 |
|---|---|---|---|
| `test_1login` | 输入账号密码登录 | 标题 = 统一门户 | 无 |
| `test_2select_scxt` | 点「发布系统」并切到新标签页 | 标题 = 工作台 - 云媒资系统 | 无 |
| `test_3material` | 内容管理 → 素材库 | 标题 = 素材库 - 云媒资系统 | 无 |
| `test_4uplaodbutton` | 选视频 → 填作者/手机号/上报单位 → 上传 → 确定 | 标题未跳走 | **上传视频** |
| `test_5keyword` | 关键字查询 → 重置 | 标题未跳走 | 无 |
| `test_6submitted` | 刷新 → 报送 → 选初审人 → 保存 | 标题未跳走 | **提交报送单** |

> 业务术语：**报送** = 把素材提交进审核流程；**初审人** = 第一级审核人。
> 前 3 条无副作用，组成**冒烟集**（`--suite smoke`）；全量含后 3 条，会写真实数据。

---

## 四、目录结构

```
├── Common/                     公共层
│   ├── basePage.py             基类：浏览器、定位、交互、上传、截图（唯一直接调 Playwright）
│   ├── elementPage.py          元素表：全工程 xpath 集中地
│   ├── runner.py               执行器：收集用例 → 执行 → 出 HTML 报告 + JUnit XML
│   ├── config.py               config.ini 读写 + UI_* 环境变量覆盖
│   ├── configOverWriter.py     定制 ConfigParser（保留键大小写 + 关闭 % 插值）
│   ├── log.py                  日志：All_Logs 全量 + Error_Logs 仅错误 + 控制台
│   ├── dir_config.py           路径常量 + ensure_dir()
│   ├── sentMail.py             报告邮件发送（QQ 邮箱 SMTP）
│   ├── junitReport.py          JUnit XML 输出（给 Jenkins 解析）
│   ├── ci.py                   CI 环境识别与构建元信息
│   ├── HTMLTestReportCN.py     中文版 HTML 报告模板（第三方）
│   ├── HTMLTestRunnerNew.py    英文版旧模板（第三方遗留，未被引用）
│   ├── configTest.py / logTest.py   手工验证脚本（可跳过）
├── PageObject/                 页面对象层
│   ├── loginPage.py            门户登录 + 素材库操作（在用）
│   └── homePage.py             工作台（空壳占位）
├── TestSuites/                 用例层（UI 用例，会被 run.py 执行）
│   ├── testLogin.py            主用例集（6 条串行业务用例）
│   └── testHome.py             空占位模块
├── tests/                      框架单元测试（不开浏览器、零业务副作用）
│   ├── helpers.py              测试基座：假 Page(FakePage/FakeLocator)、临时目录隔离、沙盒
│   ├── samples.py              样例用例类（故意失败/出错等，不参与收集）
│   ├── run_unit_tests.py       一键入口：python tests/run_unit_tests.py
│   └── test_*.py               15 个模块的测试（含测试基座自检）
├── docs/                       使用文档 + CI/CD 接入指南
├── Logs/  img/  Report/        运行产物（已 gitignore）
├── config.ini                  本地配置（含账号密码，已 gitignore）
├── config.ini.example          配置模板（入库）
├── run.py / entrance.py        入口（主 / 备用）
├── getcwd.py                   返回工程根目录
├── Jenkinsfile                 Jenkins 声明式流水线
├── Dockerfile                  容器化运行环境（Chromium + 中文字体）
├── requirements.txt            依赖清单
└── .gitignore / .dockerignore  忽略规则
```

---

## 五、分层规则（写新代码时请遵守）

| 层 | 允许做的事 | 禁止做的事 |
|---|---|---|
| `TestSuites/` 用例层 | 调页面对象方法、写断言 | 不写 xpath、不直接点元素 |
| `PageObject/` 页面对象层 | 一个方法 = 一个业务动作 | 不写断言、不出现 xpath 字符串 |
| `Common/basePage.py` | 封装浏览器与元素操作 | 不掺业务逻辑 |
| `Common/elementPage.py` | 只放元素定位值 | 不放业务代码 |

一句话：**用例层管断言，页面对象管动作，基类管操作，元素表管定位。**

数据规则（与分层同级）：**业务数据只从 config.ini 来**（`Config.require`，缺配即报错），
技术参数可留代码默认值（`Config.get/get_int/get_float` + `[tuning]`/`[timing]`/`[paths]`）；
任何一层都**禁止**把标题、账号、路径、等待秒数写死在代码里。

---

## 六、运行方式

```bash
# ---- 用例选择 ----
python run.py                                      # 全量
python run.py --suite smoke                        # 冒烟（前 3 条，不写数据）
python run.py --cases test_1login,test_2select_scxt # 自定义过滤（优先级高于 --suite）
python run.py --pattern 'test_Login*.py'           # 按文件名过滤

# ---- 运行环境 ----
python run.py --no-headless                        # 有界面跑，方便盯流程
python run.py --headless                           # 无头跑，与流水线一致
python run.py --url http://测试环境/sso-portal/#/?redirect=/launch
python run.py --browser firefox

# ---- 报告 ----
python run.py --title 冒烟测试 --filename smoke.html
python run.py --junit none                         # 不生成 JUnit XML
python run.py --mail                               # 跑完发邮件
python run.py --notify                             # 跑完推送到钉钉/企业微信群
python run.py --system erp                         # 切换到 config.ini 的 [system_erp] 系统

# ---- 其他 ----
python entrance.py                                 # 备用入口（报告主题取自 config.ini [entrance]）
python -m Common.logTest                           # 单独验证日志
python -m Common.sentMail                          # 单独验证发信
python -m Common.ci                                # 查看当前 CI 上下文
python run.py --help                               # 全部参数
```

**参数优先级**：`命令行 / 环境变量` > `config.ini` > `代码默认值`（默认值只存在于"技术参数"里；
业务数据没有默认值，缺配置直接报错）。

---

## 七、配置说明（config.ini）

完整配置项说明见 [使用文档 5.3](docs/使用文档.md#53-配置项一览configini)。

**零硬编码纪律**（全框架统一，读代码前必须知道）：

- **业务数据**（标题文案、账号、路径、断言期望、报告元信息、邮件文案）：代码里**没有兜底默认值**，
  一律 `Config.require` 读取 —— 没配就报错，报错文案直接给出「段 + 键 + 对应环境变量」，退出码 2。
- **技术参数**（超时、轮询间隔、视口、目录名）：`Config.get / get_int / get_float` 读取，
  代码里有合理默认值 —— 不配也能跑，配置只为按环境调优。
- **元素 xpath** 是定位代码，集中在 `Common/elementPage.py`，不进配置文件。

常用几项：

| 段 | 键 | 说明 |
|---|---|---|
| `[environment]` | `url`（**必填**） / `browser` | 被测地址；浏览器 chrome / firefox / edge |
| `[testcase]` | `login_username` / `login_password` | 登录账号（**必填**，CI 上用环境变量注入） |
| `[testcase]` | `upload_file` / `query_keyword` / `phone` / `reporting` | 上传视频路径、查询关键字、表单字段（均**必填**；`video_author` 可留空自动用账号） |
| `[testcase]` | `upload_mode` | `set_input_files`（默认，推荐，直接赋值；旧值 `send_keys` 等价）/ `autoit`；技术参数 |
| `[assert]` | `title_login` / `title_workbench` / `title_material` | 断言用的期望标题（**必填**）；换系统只改这里 |
| `[system]` | `username_placeholder` 等 | GenericPage 接入新系统的"文案特征"（前三项**必填**） |
| `[report]` | `title` / `filename` / `tester` / `description` | 报告元信息（**必填**，`--title` 等参数可覆盖） |
| `[entrance]` | `title` | entrance.py 备用入口的报告主题（用该入口时**必填**，不再写死在代码） |
| `[ci]` | `headless` | 无头开关（**流水线必须 true**） |
| `[ci]` | `element_timeout` / `implicit_wait` | 等待时长；Playwright 用 `element_timeout` 作默认动作超时，`implicit_wait` 保留兼容（无独立隐式等待），CI 慢时可调大 |
| `[ci]` | `smoke_cases` / `junit_xml` | 冒烟用例关键字；JUnit XML 路径（留空自动按 `[paths]` 拼） |
| `[paths]` | `report_dir` / `img_dir` / `case_pattern` 等 | 用例目录、报告目录、截图目录名、JUnit 文件名 —— 改产出物位置只动这里（可选，带默认值） |
| `[tuning]` | `viewport` / `window_poll_interval` / `notify_timeout` 等 | 浏览器与框架的调优参数（可选，带默认值） |
| `[timing]` | `menu_load` / `upload_progress` 等 | 用例各步骤的页面节奏等待秒数 —— 慢环境调大、快环境调小（可选，带默认值） |
| `[sender]` / `[addressed]` | `mail_subject` / `mail_body` 等 | 邮件发件人（含 SMTP 授权码）、收件人、主题/正文模板（`{date}` `{filename}` 占位） |

### 环境变量覆盖（CI 的关键机制）

任意配置项都能被同名环境变量覆盖，规则 `UI_ + 段名 + _ + 键名`（全大写）：

```bash
set UI_ENVIRONMENT_URL=http://测试环境/portal      # 覆盖 [environment] url
set UI_CI_HEADLESS=true                            # 开启无头
set UI_TESTCASE_LOGIN_PASSWORD=xxx                 # 覆盖密码（CI 上用凭据注入）
```

日志里会出现 `配置 [environment] url 被环境变量 UI_ENVIRONMENT_URL 覆盖`。
要整体关闭该机制：`UI_ENV_OVERRIDE=false`。

---

## 八、CI/CD 接入

工程已为流水线做好适配，接入只需配置 Jenkins：

| 已具备的能力 | 实现位置 |
|---|---|
| 标准退出码（0/1/2） | `run.py` → `sys.exit(summary['exit_code'])` |
| JUnit XML 结果（用例明细 + 趋势图） | `Common/junitReport.py` |
| 无头模式（节点无桌面也能跑） | `[ci] headless` / `UI_CI_HEADLESS` |
| 冒烟子集（不写数据，可每次构建跑） | `--suite smoke` / `[ci] smoke_cases` |
| 凭据注入（密码不落仓库） | `UI_TESTCASE_LOGIN_*` 环境变量覆盖 |
| 构建上下文写进报告 | `Common/ci.py` |
| 流水线定义与容器镜像 | `Jenkinsfile` / `Dockerfile` |
| 操作自动重试（降偶发失败） | `BasePage.retry/safe_click` / `[ci] retry_times` |
| 数据驱动用例（CSV/JSON/Excel） | `Common/dataDriver.py` |
| 多系统一键切换 | `--system` / `[system_名称]` 段 |
| 群机器人结果通知 | `Common/notifier.py` / `--notify` |

**5 步接入**：

1. Jenkins 新建 Pipeline 任务，脚本路径填 `Jenkinsfile`
2. 创建两个 Secret text 凭据：`ui-test-login-username`、`ui-test-login-password`
3. 首次执行选 `TEST_SUITE=smoke`、填 `TARGET_URL`（**测试环境**）
4. 确认构建页面出现「测试结果」与 HTML 报告链接
5. 在上游构建任务里加 Post-build Action 触发本任务

详细步骤、参数说明、容器化方案与排查表见 **[docs/CI-CD接入指南.md](docs/CI-CD接入指南.md)**。

---

## 九、框架单元测试

`tests/` 是**框架自身的单元测试**：不开浏览器、不联网、几秒跑完、零业务副作用。
改过 `Common/` 或 `PageObject/` 里的代码后，先跑它，再跑 UI 用例 ——
这样能把"框架坏了"和"页面改版了"两类问题分开。

```bash
python tests/run_unit_tests.py                          # 全部（推荐）
python tests/run_unit_tests.py tests.test_config         # 只跑某个模块（几秒）
python tests/run_unit_tests.py --junit Report/unit-tests.xml   # 流水线用
```

- 退出码与 `run.py` 一致：`0` 通过、`1` 有失败
- 当前覆盖 **439 项 / 18 个模块**：配置、日志、CI 适配、元素表、基类、执行器、JUnit 报告、
  报告模板、邮件、页面对象、用例层结构、命令行入口、测试基座自检
- 全程 2 秒左右，跑完不留痕：不写工程 `Logs/`、不在系统临时目录留垃圾目录
  （日志走 `UI_LOG_DIR` 重定向，临时目录登记到 `atexit` 统一回收）
- 流水线里的 `框架单元测试` stage 已经接好（见 `Jenkinsfile`），比 UI 用例更早发现问题

常用开关：

| 变量 | 作用 |
|---|---|
| `UT_SHOW_LOG=1` | 打开工程日志，排查单测失败原因 |
| `UT_KEEP_LOG=1` | 日志留在工程 `Logs/` 下（会污染工作区，仅需留证时用） |

写新测试的三条铁律与两个坑（样例用例类不能被 discover 收走、`load_tests` 白名单）
见 **[docs/使用文档.md](docs/使用文档.md) 第八章**。

---

## 十、新人最容易踩的坑

1. **用例有严格顺序**：`test_1login` → `test_6submitted` 是一条业务流，
   方法名的数字前缀就是用来固定顺序的。**不要改方法名**，也**不能只跑中间某一条**。
2. **切换标签页**：点「发布系统」会新开标签页，不调 `switch_other_window()`
   后续元素全都定位不到 —— 这是最常见的"莫名失败"原因。
3. **元素定位是绝对 xpath**：`elementPage.py` 里很多 `/html/body/div[4]/...`，
   前端多套一层 div 就失效。改版后优先检查 `submission`、`first_trial`、
   `person_of_first_instance` 三条。
4. **`get_element()` 找不到元素返回 `None` 而不抛异常**，调用方必须判空，
   否则会得到 `AttributeError: 'NoneType' object has no attribute 'click'`。
5. **上传文件必须存在**：`upload_file` 配的路径不存在时，上传用例会以
   `FileNotFoundError` 失败（冒烟集不含该用例）。
6. **别用 print 打日志**：控制台看得到但文件里没有，事后无法回溯。统一用 `log1`。
7. **改用户例方法名要同步改 `[ci] smoke_cases`**，否则冒烟集会失配（日志会打印「命中 N/M 条」）。
8. **生产环境警告**：`config.ini` 默认指向生产环境。跑全量前务必确认，
   流水线请用 `UI_ENVIRONMENT_URL` 指向测试环境。
9. **「一条用例都没收集到」先看日志，别急着改代码**。四条硬性约束：
   `TestSuites/__init__.py` 不能删；文件名必须以 `test` 开头；文件名只能是
   字母/数字/下划线（`test-login.py` 会被 discover **静默跳过**）；
   子目录同样需要 `__init__.py`，否则整棵子树被跳过。
   ⚠️ Windows 的文件名匹配不区分大小写、Linux 区分，
   所以 `TestLogin.py` 这类命名会出现"**本机能跑、Jenkins 收集不到**"。
   现在日志会直接给出中文原因与目录体检结果，完整对照表见
   [使用文档 7.1](docs/使用文档.md#71-一条用例都没收集到深度排查)。
10. **中文输出不要依赖终端编码**：入口已把 stdout/stderr 强制为 UTF-8，
   Linux 容器里 locale 为 `C` 时也不会因 `print` 中文而崩。
11. **缺配置报错不是 bug，也别去代码里加默认值**：框架零硬编码 ——
   必填业务数据（标题/账号/路径/报告元信息…）没配时，日志会出现
   `缺少必需配置：config.ini 的 [段] 键（对应环境变量 UI_段_键）`，
   退出码 2（ABORTED，用例根本没跑）。按提示补配置即可；
   技术参数（`[tuning]`/`[timing]`/`[paths]`/超时类）不配也能跑，用代码默认值。

---

## 十一、已知限制与改进方向

- 用例之间无数据隔离，任一步失败会导致后续用例连续失败 —— 排错时优先修**最早失败**的那条。
- 断言以页面标题为主，业务数据（上传成功、查询命中数、报送状态）未被断言；
  `BasePage.get_text()` 已提供能力，可逐步补强。
- 元素定位以绝对 xpath 为主，建议逐条替换为相对/语义定位（按文本、按属性）。
- 因用例强串行依赖，**不适合**做单用例失败重试；若将来用例解耦可引入。
- `config.ini` 本地为明文，建议本机也不要放生产账号；CI 上统一用 Jenkins 凭据。
- 可删的遗留文件（全工程零引用）：`Common/HTMLTestRunnerNew.py`、
  `PageObject/homePage.py`、`TestSuites/testHome.py`。
