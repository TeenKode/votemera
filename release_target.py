"""Resolve the latest published, non-prerelease Votemera release."""
import json,re,urllib.request
URL='https://api.github.com/repos/TeenKode/votemera/releases/latest'

def version_tuple(version):
    if not re.fullmatch(r'\d+\.\d+\.\d+',version): raise ValueError('Invalid version')
    return tuple(map(int,version.split('.')))

def latest_release():
    req=urllib.request.Request(URL,headers={'User-Agent':'Votemera-Updater','Accept':'application/vnd.github+json'})
    with urllib.request.urlopen(req,timeout=10) as response:
        data=json.load(response)
    tag=data['tag_name']
    if data['draft'] or data['prerelease'] or not re.fullmatch(r'v\d+\.\d+\.\d+',tag): raise ValueError('No stable release')
    return {'tag':tag,'version':tag[1:]}

if __name__=='__main__':
    import sys
    try:
        release=latest_release()
        if len(sys.argv)>1 and version_tuple(release['version'])<=version_tuple(sys.argv[1]): print('current')
        else: print(release['tag'])
    except (OSError,ValueError,KeyError) as e:
        print('Не удалось определить стабильный релиз: '+str(e),file=sys.stderr)
        sys.exit(1)
