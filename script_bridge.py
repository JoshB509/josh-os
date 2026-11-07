"""Desktop handoff CLI. No HTTP, credentials, external model or title guessing."""
import argparse,json,sys
from pathlib import Path
from studio.store import Store
from studio.writing import Writing
from studio.control import Control
from studio.writing_context import Context


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser();p.add_argument('--data',default=str(Path(__file__).parent/'Data'))
    sub=p.add_subparsers(dest='action',required=True);sub.add_parser('list-projects')
    imp=sub.add_parser('import-script');imp.add_argument('--request',required=True)
    get=sub.add_parser('get-script');get.add_argument('--project-id',required=True)
    approve=sub.add_parser('approve-script');approve.add_argument('--project-id',required=True);approve.add_argument('--version-id',required=True);approve.add_argument('--expected-revision',required=True,type=int);approve.add_argument('--note',required=True)
    status=sub.add_parser('status');status.add_argument('--project-id')
    start=sub.add_parser('start-edit');start.add_argument('--project-id',required=True);start.add_argument('--expected-revision',required=True,type=int);start.add_argument('--request-id',required=True)
    register=sub.add_parser('register-artifact');register.add_argument('--project-id',required=True);register.add_argument('--kind',choices=['html','greenscreen'],required=True);register.add_argument('--path',required=True);register.add_argument('--expected-revision',required=True,type=int)
    artifact=sub.add_parser('approve-artifact');artifact.add_argument('--project-id',required=True);artifact.add_argument('--artifact-id',required=True);artifact.add_argument('--identity',required=True)
    attach=sub.add_parser('attach-run');attach.add_argument('--project-id',required=True);attach.add_argument('--path',required=True);attach.add_argument('--expected-revision',required=True,type=int)
    prop=sub.add_parser('propose-edit');prop.add_argument('--request',required=True)
    resolve=sub.add_parser('resolve-proposal');resolve.add_argument('--project-id',required=True);resolve.add_argument('--proposal-id',required=True);resolve.add_argument('--accept',action='store_true')
    context=sub.add_parser('writing-context');context.add_argument('--project-id',required=True);context.add_argument('--expected-revision',required=True,type=int);context.add_argument('--start',required=True,type=int);context.add_argument('--end',required=True,type=int);context.add_argument('--rhetorical-job',required=True);context.add_argument('--pass-id');context.add_argument('--scope',choices=['selection','full_script'],default='selection')
    a=p.parse_args();store=Store(a.data);writing=Writing(store)
    if a.action=='list-projects':result=[dict(project_id=x['id'],name=x['name'],revision=x['revision'],archived=x['archived']) for x in store.projects()]
    elif a.action=='import-script':result=writing.import_script(json.loads(Path(a.request).read_text(encoding='utf-8-sig')))
    elif a.action=='get-script':result=writing.workspace(a.project_id)
    elif a.action=='propose-edit':result=writing.propose(json.loads(Path(a.request).read_text(encoding='utf-8-sig')))
    elif a.action=='resolve-proposal':result=writing.resolve_proposal(a.project_id,a.proposal_id,a.accept)
    elif a.action=='writing-context':result=writing.context(Path(__file__).parent,a.project_id,a.expected_revision,a.start,a.end,a.rhetorical_job,a.pass_id,a.scope)
    elif a.action=='approve-script':result=writing.approve(a.project_id,a.version_id,a.expected_revision,a.note)
    else:
        control=Control(store,Path(__file__).parent)
        if a.action=='status':result=control.status(a.project_id) if a.project_id else [control.status(p['id']) for p in store.projects() if not p['archived']]
        elif a.action=='start-edit':result=control.start(a.project_id,a.expected_revision,a.request_id)
        elif a.action=='register-artifact':result=control.register(a.project_id,a.kind,a.path,a.expected_revision)
        elif a.action=='approve-artifact':result=control.approve(a.project_id,a.artifact_id,a.identity)
        else:result=control.attach_run(a.project_id,a.path,a.expected_revision)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
