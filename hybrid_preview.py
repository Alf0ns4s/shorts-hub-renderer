"""Isolated private source regression/composition check; no upload credentials."""
import os,re,subprocess,sys,tempfile
from pathlib import Path
from worker import git_env,SOURCE

def main():
    ref=os.environ.get('PREVIEW_SOURCE_REF','feature/playful-hybrid-v1')
    if not re.fullmatch(r'feature/playful-hybrid-v1',ref):
        print('Unsupported preview source ref.');return 1
    token=os.environ['SOURCE_TOKEN'];auth=git_env(token)
    child=os.environ.copy()
    for key in list(child):
        if key in ('SOURCE_TOKEN','YT_SECRETS','GROQ_API_KEY','PAGES_TOKEN','GITHUB_TOKEN','GH_TOKEN') or key.startswith(('GIT_CONFIG_','ACTIONS_','RUNNER_')):
            child.pop(key,None)
    child.update(PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8')
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);repo=root/'source';logpath=root/'diagnostics.log'
        def run(args,env=None,check=True):
            with logpath.open('a',encoding='utf8') as log:
                p=subprocess.run(args,cwd=repo if repo.exists() else root,env=env or child,stdout=log,stderr=subprocess.STDOUT,timeout=1800)
            if check and p.returncode:raise RuntimeError('Private preview command failed')
            return p.returncode
        success=False
        try:
            run(['git','clone','--depth','1','--branch',ref,SOURCE,str(repo)],auth)
            run([sys.executable,'-m','pip','install','-r','requirements.txt'])
            run([sys.executable,'-m','unittest','discover','-s','tools','-p','test_*.py'])
            run([sys.executable,'tools/hybrid_smoke.py','--video'])
            success=True
        except Exception:
            pass
        if repo.exists():
            output=repo/'state/hybrid_pilot';output.mkdir(parents=True,exist_ok=True)
            text=logpath.read_text(encoding='utf8',errors='replace') if logpath.exists() else ''
            import base64
            for value in (token,base64.b64encode(('x-access-token:'+token).encode()).decode()):text=text.replace(value,'[redacted]')
            (output/'diagnostics.log').write_text(text,encoding='utf8')
            (output/'cloud-status.txt').write_text('PASS' if success else 'FAIL',encoding='utf8')
            try:
                run(['git','config','user.name','shorts-hub-worker'],auth)
                run(['git','config','user.email','shorts-hub-worker@users.noreply.github.com'],auth)
                files=[str(p.relative_to(repo)) for p in output.iterdir() if p.suffix in ('.jpg','.log','.txt') or p.name=='results.json']
                run(['git','add','-f','--',*files],auth)
                if run(['git','diff','--cached','--quiet'],auth,check=False):
                    run(['git','commit','-m','Private hybrid preview: cloud regression and composition results'],auth)
                    run(['git','pull','--rebase','origin',ref],auth)
                    run(['git','push','origin','HEAD:'+ref],auth)
            except Exception:
                print('Private preview diagnostics could not be saved.');return 1
        print('Private hybrid preview passed. Results are in the private source branch.' if success else 'Private hybrid preview failed. Check private branch diagnostics.')
        return 0 if success else 1

if __name__=='__main__':sys.exit(main())
