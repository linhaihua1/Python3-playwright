# ==========================================================================
#  容器化运行环境 —— UI 自动化测试（Playwright 版）
# ==========================================================================
#
#  【这是什么】
#    一个"自带 Python + Playwright + Chromium"的镜像。让 UI 测试无需在 Jenkins
#    节点上装浏览器，直接跑在容器里。适合 Jenkins 使用 Kubernetes / Docker agent
#    的场景，也方便开发同学本地用一条 docker run 复现"流水线上才失败"的问题。
#
#  【怎么用】
#    1) 构建镜像
#         docker build -t ui-auto-test:latest .
#    2) 跑冒烟测试（把被测地址用环境变量传进去，不要写死在镜像里）
#         docker run --rm \
#           -e UI_ENVIRONMENT_URL=http://测试环境地址/sso-portal/#/?redirect=/launch \
#           -e UI_TESTCASE_LOGIN_USERNAME=你的账号 \
#           -e UI_TESTCASE_LOGIN_PASSWORD=你的密码 \
#           -v "$PWD/Report:/app/Report" \
#           -v "$PWD/Logs:/app/Logs" \
#           ui-auto-test:latest
#    3) 在 Jenkinsfile 里改用容器 agent（把顶层 agent any 换掉）：
#         agent {
#             docker {
#                 image 'ui-auto-test:latest'
#                 args  '--shm-size=2g'      // /dev/shm 太小会导致浏览器崩溃
#             }
#         }
#       容器里已是无头模式（见下方 UI_CI_HEADLESS），无需额外配置。
#
#  【为什么这样装】
#    python -m playwright install --with-deps chromium
#        浏览器由 Playwright 自己管理（落在容器内 /root/.cache/ms-playwright），
#        --with-deps 一并装齐 Chromium 运行所需的全部系统库。
#        因此**不再需要 apt 装 chromium / chromium-driver**，也不再需要
#        配置 [ci] chrome_binary —— 留空即用自带浏览器。
#    fonts-noto-cjk / wqy-zenhei  中文字体。缺字体时页面文字会渲染成方块，
#                                 会导致按文本定位的元素找不到，也会让失败截图失去价值
#    tzdata                       时区数据，保证日志时间与本地一致
#
#  【注意】
#    镜像是通用的，**不要**把账号密码、被测地址烤进镜像层。
#    所有环境相关的东西都通过 -e 环境变量或 Jenkins 凭据注入。
# ==========================================================================
FROM python:3.11-slim

# 环境变量说明（注意：Dockerfile 续行里不放 # 注释，避免被当成值的一部分）：
#   PYTHONIOENCODING / LANG / LC_ALL
#       保证中文日志与报告结论不乱码、不抛 UnicodeEncodeError。
#       默认 C/POSIX locale 会让 Python 的 stdout 编码退化成 ascii。
#   PLAYWRIGHT_BROWSERS_PATH
#       把 Playwright 浏览器固定在系统级路径，避免运行期再往用户目录下载。
#   UI_CI_HEADLESS        容器没有桌面，强制无头，避免误起有头模式导致浏览器启动失败
#   UI_CI_JUNIT_XML       报告与结果固定输出到 /app/Report
ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai \
    PYTHONIOENCODING=utf-8 \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    LANGUAGE=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    UI_CI_HEADLESS=true \
    UI_CI_JUNIT_XML=Report/junit-results.xml

# 系统依赖：中文字体 + 证书 + 时区（浏览器本体与运行库交给 playwright install --with-deps）
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
        fonts-wqy-zenhei \
        ca-certificates \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先只复制依赖清单再安装：requirements.txt 没变时可直接复用这层缓存，
# 改业务代码不会触发重新安装依赖
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# 再复制工程代码
COPY . /app

# 默认命令：跑冒烟子集（不产生数据副作用），生成 JUnit XML 与 HTML 报告。
# 需要全量回归时用 docker run 覆盖：docker run ... ui-auto-test:latest python run.py --suite full --headless
CMD ["python", "run.py", "--suite", "smoke", "--headless", "--junit", "Report/junit-results.xml"]
