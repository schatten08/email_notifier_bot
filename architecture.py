from diagrams import Diagram, Cluster, Edge
from diagrams.azure.identity import ActiveDirectory
from diagrams.azure.general import Subscriptions
from diagrams.onprem.container import Docker
from diagrams.onprem.vcs import Gitlab
from diagrams.onprem.ci import GithubActions
from diagrams.generic.os import Windows
from diagrams.onprem.monitoring import Grafana
from diagrams.programming.language import Python
from diagrams.saas.chat import Teams

with Diagram(
    "Email Notifier Bot — Architecture",
    filename="/tmp/architecture",
    outformat="png",
    show=False,
    direction="LR",
    graph_attr={"fontsize": "16", "bgcolor": "white", "pad": "0.6"},
):

    with Cluster("Microsoft Cloud"):
        outlook = Subscriptions("Shared Mailbox\n(Outlook / Graph API)")
        azure_ad = ActiveDirectory("Azure AD\n(OAuth2 App)")

    with Cluster("ServiceNow"):
        snow = Python("Email Notifications\n(INC / RITM / SLA / NPR / ER)")

    with Cluster("Email Notifier Bot (Docker)"):
        with Cluster("bot/"):
            main    = Python("main.py\npoller + scheduler")
            parser  = Python("parser.py\nHTML → ticket/NPR/ER")
            teams_m = Python("teams.py\nAdaptive Card builder")
            reports = Python("reports.py\nweekly NPR/ER report")
            storage = Python("storage.py\nBotState + dead-letter")

        with Cluster("data/ (volume)"):
            checkpoint = Windows("bot_checkpoint.json")
            report_f   = Windows("weekly_report*.json")
            token_f    = Windows("o365_token.txt")
            dead       = Windows("dead_letters.json")

        main >> Edge(label="parse") >> parser
        main >> Edge(label="send card") >> teams_m
        main >> Edge(label="collect") >> reports
        main >> Edge(label="state") >> storage
        storage >> checkpoint
        storage >> report_f
        storage >> token_f
        storage >> dead

    with Cluster("Microsoft Teams"):
        cis_ch    = Teams("CIS Channel\nKZ / UZ / KG")
        me_ch     = Teams("Middle East Channel\nUAE / QA / SA / KW / OM / JO")
        time_ch   = Teams("Time Reminder\n+ Evening message")
        report_ch = Teams("Weekly Report\nNPR / ER")

    uptime = Grafana("Uptime Kuma\n(heartbeat 30s)")

    with Cluster("CI / CD"):
        gitlab_ci  = Gitlab("GitLab CI\nself-hosted runner")
        gh_actions = GithubActions("GitHub Actions\nmanual fallback")
        server     = Docker("Prod Server\ndocker-compose")

    snow >> Edge(label="emails") >> outlook
    azure_ad >> Edge(label="token") >> main
    outlook >> Edge(label="Graph API\npoll every 60s") >> main

    teams_m >> Edge(label="INC/RITM/SLA") >> cis_ch
    teams_m >> Edge(label="ME tickets") >> me_ch
    teams_m >> Edge(label="Friday reminder") >> time_ch
    reports >> Edge(label="Friday 18:00 Astana") >> report_ch
    main >> Edge(label="heartbeat") >> uptime

    gitlab_ci  >> Edge(label="autodeploy") >> server
    gh_actions >> Edge(label="SSH fallback") >> server
