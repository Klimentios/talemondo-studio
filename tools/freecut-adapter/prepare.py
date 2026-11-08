"""Apply the minimal TaleMondo overlay to the pinned, unmodified upstream checkout."""
from pathlib import Path
import shutil
root=Path(__file__).resolve().parent.parent/'freecut-upstream'
shutil.copyfile(Path(__file__).with_name('bridge.ts'),root/'src/talemondo-bridge.ts')
p=root/'src/main.tsx';s=p.read_text();s=s.replace("if (import.meta.env.PROD && 'serviceWorker' in navigator)","if (false && import.meta.env.PROD && 'serviceWorker' in navigator)")
if "import './talemondo-bridge'" not in s:s="import './talemondo-bridge'\n"+s
p.write_text(s)
shutil.copyfile(Path(__file__).with_name('workspace-gate.tsx'),root/'src/features/workspace-gate/workspace-gate.tsx')
p=root/'src/components/brand/freecut-logo.tsx';s=p.read_text().replace('        FreeCut\n','        TaleMondo\n');p.write_text(s)
shutil.copyfile(Path(__file__).resolve().parents[2]/'preview/h3-director/assets/talemondo-mark.svg',root/'public/talemondo-mark.svg')

p=root/'src/app/route-error.tsx';s=p.read_text();needle='export function RouteErrorScreen({ error, reset }: ErrorComponentProps) {'
if '/bridge/error' not in s:
 s=s.replace(needle,needle+"\n  useEffect(()=>{void fetch('/bridge/error',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({error:formatRouteErrorDetails(error,window.location.href)})})},[error])")
p.write_text(s)
# Remove upstream promotional entry points at source, keeping editing utilities.
import re
for relative in ['src/routes/projects/index.lazy.tsx','src/features/editor/components/toolbar.tsx']:
 p=root/relative;s=p.read_text()
 s=re.sub(r'<Button\b[^>]*\basChild[^>]*>\s*<(?:a|Link)\b(?:(?!</Button>).)*?(?:github\.com/walterlow/freecut|DISCORD_INVITE_URL|(?:to|href)="/docs")(?:(?!</Button>).)*?</Button>', '', s, flags=re.S)
 s=s.replace('<WorkspaceIndicator />','')
 p.write_text(s)
p=root/'src/components/brand/freecut-logo.tsx';s=p.read_text();a=s.index('function ScissorIcon(');b=s.index('\nexport function FreeCutLogo',a);s=s[:a]+'''function ScissorIcon({ className }: { className?: string }) {
 return <img src="/talemondo-mark.svg" alt="" className={className} style={{objectFit:'contain'}} />
}
'''+s[b:];p.write_text(s)
shutil.copyfile(Path(__file__).with_name('studio-theme.css'),root/'src/talemondo-theme.css')
p=root/'src/main.tsx';s=p.read_text()
if "import './talemondo-theme.css'" not in s:s=s.replace("import './index.css'","import './index.css'\nimport './talemondo-theme.css'")
p.write_text(s)
p=root/'src/i18n/index.ts';s=p.read_text().replace('    resources,','    resources,\n    lng: localStorage.getItem(I18N_STORAGE_KEY) || \'ru\',') if "lng: localStorage.getItem" not in p.read_text() else p.read_text();p.write_text(s)
p=root/'src/routes/index.tsx';p.write_text("import {createFileRoute, redirect} from '@tanstack/react-router'\nexport const Route=createFileRoute('/')({beforeLoad:()=>{throw redirect({to:'/projects'})}})\n")
shutil.copyfile(Path(__file__).with_name('browser-compat.js'),root/'src/talemondo-compat.js')
p=root/'src/main.tsx';s=p.read_text()
if "import './talemondo-compat.js'" not in s:s="import './talemondo-compat.js'\n"+s
p.write_text(s)
