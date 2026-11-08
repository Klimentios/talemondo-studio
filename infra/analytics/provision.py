"""Generate localhost-only Grafana config and panels for Studio JSON history."""
import json
import os
import secrets
from pathlib import Path
root=Path.home()/'Library/Application Support/H3 Analytics Grafana'
for sub in ['data','logs','plugins','provisioning/datasources','provisioning/dashboards','dashboards']:(root/sub).mkdir(parents=True,exist_ok=True)
credentials=root/'admin-password'
if not credentials.exists():credentials.write_text(secrets.token_urlsafe(32));credentials.chmod(0o600)
config=f'''[server]
http_addr = 127.0.0.1
http_port = 18210
root_url = http://127.0.0.1:18210/
[paths]
data = {root}/data
logs = {root}/logs
plugins = {root}/plugins
provisioning = {root}/provisioning
[security]
admin_user = studio
admin_password = {credentials.read_text().strip()}
allow_embedding = true
[auth.anonymous]
enabled = true
org_role = Viewer
[users]
allow_sign_up = false
[analytics]
reporting_enabled = false
check_for_updates = false
'''
(root/'grafana.ini').write_text(config);(root/'grafana.ini').chmod(0o600)
base=os.environ.get('H3_ANALYTICS_API_URL','http://127.0.0.1:18208')
(root/'provisioning/datasources/studio.yaml').write_text('''apiVersion: 1
datasources:
  - name: STUDIO history
    uid: studio-history
    type: yesoreyeram-infinity-datasource
    access: proxy
    isDefault: true
    editable: false
    jsonData:
      allowedHosts:
        - "'''+base+'''"
''')
(root/'provisioning/dashboards/studio.yaml').write_text(f'''apiVersion: 1
providers:
  - name: STUDIO
    orgId: 1
    folder: STUDIO
    type: file
    disableDeletion: true
    updateIntervalSeconds: 30
    options:
      path: {root}/dashboards
''')
# Native Grafana owns filters, charts, tables and time range.
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from local_studio.analytics_grafana import build_dashboard
dashboard=build_dashboard(base)
(root/'dashboards/social.json').write_text(json.dumps(dashboard,ensure_ascii=False,indent=2))
print('Provisioned local social dashboard')
