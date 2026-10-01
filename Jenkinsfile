// ==========================================================================
//  Jenkins 声明式流水线 —— 融媒体生产系统 UI 自动化测试
// ==========================================================================
//
//  【这个文件干什么】
//    让 Jenkins 在代码构建完成后自动拉起 UI 冒烟测试，并把结果反馈到构建页面上：
//      代码提交/构建成功 -> 流水线触发 -> 跑冒烟用例 -> 出报告 -> 归档 -> 通知
//
//  【怎么用（三步）】
//    1. Jenkins 新建「Pipeline」任务（或「多分支流水线」，它会自动识别本文件）
//    2. Pipeline script from SCM，指向本仓库，脚本路径填 Jenkinsfile
//    3. 首次执行前准备：
//       - 执行节点上装好 Python 3.9+（无需安装 Chrome —— Playwright 自管浏览器，
//         由「准备运行环境」阶段的 playwright install 自动下载）
//       - 创建两个「Secret text」凭据（见下方 withCredentials 的说明）
//       - 安装插件：JUnit、HTML Publisher、Timestamps（可选）
//       - 节点首次构建需要联网下载浏览器（约 150MB，之后走缓存）
//
//  【三种触发方式（按需选一种或组合）】
//    A. 上游构建任务触发（推荐，最贴合"构建后自动冒烟"）：
//       在「构建任务」的 Post-build Actions 里加
//         Build other projects -> ui-automation-smoke -> Trigger only if build is stable
//       或在构建任务的流水线里加一行：
//         build job: 'ui-automation-smoke', parameters: [string(name:'APP_BUILD_NO', value:"${env.BUILD_NUMBER}")], wait: false
//    B. 代码仓库 Webhook 触发：多分支流水线 + GitLab/GitHub 插件，推代码即跑
//    C. 定时回归：把下面的 triggers 块取消注释（工作日每天 8:30 跑一次全量）
//
//  【和工程代码的配合点（改代码时注意别破坏）】
//    1. 退出码：run.py 通过 sys.exit 返回 0/1/2，Jenkins 靠它把 stage 标红
//    2. JUnit XML：Report/junit-results.xml，junit 步骤解析它生成用例明细与趋势图
//    3. 环境变量覆盖：UI_ 开头的变量会覆盖 config.ini，
//       所以仓库里不必存生产账号密码（见 Common/config.py 的说明）
//
//  【新人注意】
//    - 本流水线默认跑 **smoke（冒烟）** 子集：只覆盖"登录 -> 进系统 -> 进素材库"，
//      不产生任何数据副作用，可以在每次构建后放心执行。
//      full（全量）会真实上传视频、报送素材，请只在测试环境或明确需要时选择。
//    - disableConcurrentBuilds 是刻意加上的：UI 用例会操作同一套环境数据，
//      两个构建同时跑必然互相干扰。
// ==========================================================================

pipeline {
    agent any

    // ----------------------------------------------------------------------
    // 触发方式 C：定时回归（默认关闭，需要时取消注释）
    // ----------------------------------------------------------------------
    // triggers {
    //     cron('H 8 * * 1-5')   // 工作日每天 8:30 左右跑一次
    // }

    options {
        // 整体超时：UI 测试再慢也不该超过 40 分钟，避免卡死占住执行器
        timeout(time: 40, unit: 'MINUTES')
        // 控制台每行带时间戳，排查"卡在哪一步"时非常有用
        timestamps()
        // 只保留最近 30 次构建记录，避免磁盘被报告和截图撑满
        buildDiscarder(logRotator(numToKeepStr: '30', artifactNumToKeepStr: '30'))
        // 禁止同一个任务并行执行（UI 用例会操作同一套环境数据）
        disableConcurrentBuilds()
        // 需要手动点「继续」才执行时打开（例如全量回归防误触）
        // skipDefaultCheckout(false)
    }

    parameters {
        choice(name: 'TEST_SUITE', choices: ['smoke', 'full'],
               description: 'smoke=冒烟(登录链路，不写数据，默认)；full=全量(含上传/报送，会写真实数据)')

        string(name: 'TARGET_URL', defaultValue: '',
               description: '被测地址。留空则用仓库 config.ini 的 [environment] url；建议由上游构建任务传入')

        string(name: 'APP_BUILD_NO', defaultValue: '',
               description: '被测应用自身的构建号/版本，会写进报告与 JUnit 结果，便于追溯"这份结果对应哪个版本"')

        booleanParam(name: 'HEADLESS', defaultValue: true,
               description: '无头模式。Jenkins 节点没有桌面，必须保持 true')

        booleanParam(name: 'SEND_MAIL', defaultValue: false,
               description: '是否额外用工程内置的邮件功能把 HTML 报告发给业务方（需先配好 config.ini [sender]）')

        string(name: 'NOTIFY_MAIL', defaultValue: '',
               description: '失败通知收件人（逗号分隔）。留空则不发送通知')

        booleanParam(name: 'USE_CREDENTIALS', defaultValue: true,
               description: '从 Jenkins 凭据读取登录账号密码（推荐，密码不落仓库）。关闭则用 config.ini 里的配置')

        string(name: 'CREDENTIALS_ID', defaultValue: 'ui-test-login',
               description: '凭据 ID 前缀。实际需要两个「Secret text」凭据：<前缀>-username 与 <前缀>-password')
    }

    environment {
        // 不加这两句，Windows 节点上的中文日志会乱码甚至抛 UnicodeEncodeError
        PYTHONIOENCODING = 'utf-8'
        PYTHONUNBUFFERED = '1'
        // ★ 关键：打开无头模式。Jenkins 节点没有桌面环境，有头模式起不来浏览器。
        //   这个变量会覆盖 config.ini -> [ci] headless，见 Common/config.py
        UI_CI_HEADLESS = "${params.HEADLESS}"
        // 结果文件路径固定，方便后面的 junit 步骤引用
        UI_CI_JUNIT_XML = 'Report/junit-results.xml'
        APP_BUILD_NO = "${params.APP_BUILD_NO}"
    }

    stages {

        // ------------------------------------------------------------------
        // ① 拉取代码
        // ------------------------------------------------------------------
        stage('拉取代码') {
            steps {
                checkout scm
                script {
                    echo "分支：${env.GIT_BRANCH ?: env.BRANCH_NAME ?: '-'}"
                    echo "提交：${env.GIT_COMMIT ?: '-'}"
                }
            }
        }

        // ------------------------------------------------------------------
        // ② 准备运行环境：独立虚拟环境 + 安装依赖 + 下载 Playwright 浏览器
        //    用 .venv 而不是节点的全局 Python，避免多个任务互相污染依赖版本
        //    Playwright 浏览器首次构建需联网下载（约 150MB，之后走节点缓存）
        // ------------------------------------------------------------------
        stage('准备运行环境') {
            steps {
                script {
                    if (isUnix()) {
                        env.PYTHON_BIN = "${env.WORKSPACE}/.venv/bin/python"
                        sh '''
                            set -e
                            python3 -m venv .venv
                            .venv/bin/python -m pip install --upgrade pip
                            .venv/bin/python -m pip install -r requirements.txt
                            .venv/bin/python -m playwright install chromium
                        '''
                    } else {
                        env.PYTHON_BIN = "${env.WORKSPACE}\\.venv\\Scripts\\python.exe"
                        bat '''
                            python -m venv .venv
                            .venv\\Scripts\\python.exe -m pip install --upgrade pip
                            .venv\\Scripts\\python.exe -m pip install -r requirements.txt
                            .venv\\Scripts\\python.exe -m playwright install chromium
                        '''
                    }
                    echo "Python 解释器：${env.PYTHON_BIN}"
                }
            }
        }

        // ------------------------------------------------------------------
        // ③ 框架单元测试（质量门禁）
        //    几十秒、不开浏览器、零业务副作用。
        //    先证明"框架自身是好的"，再花几分钟跑 UI 用例 ——
        //    UI 用例失败时最容易纠结"是框架坏了还是页面改版了"，
        //    这一步能把这两类问题彻底分开。
        // ------------------------------------------------------------------
        stage('框架单元测试') {
            steps {
                script {
                    def cmd = [
                        "\"${env.PYTHON_BIN}\"", 'tests/run_unit_tests.py',
                        '--junit Report/unit-tests.xml'
                    ].join(' ')
                    echo "单元测试命令：${cmd}"
                    if (isUnix()) {
                        sh cmd
                    } else {
                        bat cmd
                    }
                }
            }
        }

        // ------------------------------------------------------------------
        // ④ 执行 UI 测试（构建后的冒烟测试就在这一步）
        //    sh/bat 返回非 0 会让本 stage 变红 —— 这正是我们要的"自动判定失败"
        // ------------------------------------------------------------------
        stage('执行冒烟测试') {
            steps {
                script {
                    def urlArg    = params.TARGET_URL?.trim()   ? "--url ${params.TARGET_URL.trim()}" : ''
                    // tester 用 ASCII，避免 Windows cmd 下中文参数编码问题
                    def testerArg = params.APP_BUILD_NO?.trim() ? "--tester app-build-${params.APP_BUILD_NO.trim()}" : ''
                    def mailArg   = params.SEND_MAIL ? '--mail' : ''
                    // 解释器路径加引号：Windows 节点的 WORKSPACE 可能含空格
                    def cmd = [
                        "\"${env.PYTHON_BIN}\"", 'run.py',
                        "--suite ${params.TEST_SUITE}",
                        '--headless',
                        '--junit Report/junit-results.xml',
                        urlArg, testerArg, mailArg
                    ].findAll { it != null && it.trim() != '' }.join(' ')

                    // 不上 --title/--filename，保持 config.ini 里配置的中文报告名，
                    // 避免中文参数在不同节点的编码差异。
                    // sh/bat 返回非 0 会直接让本 stage 变红 —— 这就是"自动判定构建失败"。
                    echo "测试命令：${cmd}"

                    if (params.USE_CREDENTIALS) {
                        // ★ 账号密码来自 Jenkins 凭据，不写进仓库、也不出现在日志里
                        //   （Jenkins 会自动把凭据值在控制台输出中脱敏）。
                        //   变量名恰好符合工程的覆盖规则：UI_<段>_<键>，
                        //   所以 Common/config.py 会自动用它覆盖 config.ini 的
                        //   [testcase] login_username / login_password。
                        withCredentials([
                            string(credentialsId: "${params.CREDENTIALS_ID}-username",
                                   variable: 'UI_TESTCASE_LOGIN_USERNAME'),
                            string(credentialsId: "${params.CREDENTIALS_ID}-password",
                                   variable: 'UI_TESTCASE_LOGIN_PASSWORD')
                        ]) {
                            if (isUnix()) {
                                sh cmd
                            } else {
                                bat cmd
                            }
                        }
                    } else {
                        if (isUnix()) {
                            sh cmd
                        } else {
                            bat cmd
                        }
                    }
                }
            }
        }
    }

    // ----------------------------------------------------------------------
    // 收尾：无论成功失败都要归档结果（放在 post 里才能保证失败时也执行）
    // ----------------------------------------------------------------------
    post {
        always {
            script {
                // 1) 解析 JUnit XML -> 构建页面出现「测试结果」，并画出通过率趋势
                //    两个文件一起解析：框架单元测试 + UI 用例
                //    allowEmptyResults：测试没跑起来时不要让归档步骤再报一次错
                junit testResults: 'Report/junit-results.xml,Report/unit-tests.xml',
                      allowEmptyResults: true,
                      keepLongStdio: true

                // 2) 归档产物：报告、日志、失败截图都存进构建，方便事后回溯
                archiveArtifacts artifacts: 'Report/**', allowEmptyArchive: true, fingerprint: true
                archiveArtifacts artifacts: 'Logs/**',    allowEmptyArchive: true
                archiveArtifacts artifacts: 'img/**',     allowEmptyArchive: true
            }

            // 3) 在构建页面内嵌展示 HTML 报告（需要 HTML Publisher 插件）
            //    注意：Jenkins 默认的 CSP 会屏蔽报告里的内联脚本/样式，
            //    如页面显示异常，需把「管理 Jenkins -> 脚本命令行」里的
            //      System.setProperty("hudson.model.DirectoryBrowserSupport.CSP", "")
            //    设置好（重启后需重设，或用 init.groovy.d 固化）。
            publishHTML(target: [
                allowMissing: true,
                alwaysLinkToLastBuild: true,
                keepAll: true,
                reportDir: 'Report',
                reportFiles: '*.html',
                reportName: 'UI 自动化测试报告'
            ])
        }

        failure {
            script {
                def to = params.NOTIFY_MAIL?.trim()
                if (to) {
                    // 用 Jenkins 内置 mail 步骤（需先在「系统配置 -> 邮件通知」里配好 SMTP）。
                    // 若装了 Email Extension 插件，可换成功能更强的 emailext：
                    //   emailext to: to, subject: "[${env.JOB_NAME}] 第 ${env.BUILD_NUMBER} 次构建失败",
                    //            body: '${BUILD_URL}', attachLog: true
                    mail to: to,
                         subject: "[${env.JOB_NAME}] 第 ${env.BUILD_NUMBER} 次构建失败",
                         body: """UI 自动化测试未通过。

任务    : ${env.JOB_NAME}
构建号  : ${env.BUILD_NUMBER}
被测版本: ${params.APP_BUILD_NO ?: '-'}
测试集  : ${params.TEST_SUITE}
地址    : ${params.TARGET_URL ?: '（取仓库配置）'}
构建页面: ${env.BUILD_URL}

排查顺序：
  1. 构建页面的「测试结果」看挂了哪条用例
  2. 归档的 Report/*.html 报告与 img/ 下的失败截图
  3. Logs/日期/Error_Logs/ 里的错误堆栈
"""
                } else {
                    echo '未配置 NOTIFY_MAIL，跳过失败通知'
                }
            }
        }

        success {
            echo '✅ UI 自动化测试全部通过'
        }

        aborted {
            echo '⚠️ 构建被中止（超时或手动取消），请检查环境是否可用'
        }
    }
}
