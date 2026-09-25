"""Prepare, review and evaluate local reference-mask projects."""
import argparse
import json
from pathlib import Path

from .evaluate import evaluate
from .project import prepare
from .store import Project


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('prepare')
    create.add_argument('--data-dir', type=Path, default=Path('data'))
    create.add_argument('--project', type=Path, default=Path('data/annotations/bananaimagebd-v1'))
    create.add_argument('--protocol', type=Path, default=Path('datasets/segmentation_protocol.json'))
    serve = commands.add_parser('serve')
    serve.add_argument('--project', type=Path, default=Path('data/annotations/bananaimagebd-v1'))
    serve.add_argument('--port', type=int, default=8001)
    measure = commands.add_parser('evaluate')
    measure.add_argument('--project', type=Path, default=Path('data/annotations/bananaimagebd-v1'))
    measure.add_argument('--partition', choices=['development', 'evaluation'], default='evaluation')
    measure.add_argument('--allow-assistant', action='store_true')
    measure.add_argument('--output', type=Path, default=Path('reports/segmentation-evaluation.json'))
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.data_dir, args.project, args.protocol)
        print(json.dumps(dict(project=str(args.project), samples=len(result['items'])), indent=2))
    elif args.command == 'serve':
        import uvicorn
        from .web import create_app
        if not 1 <= args.port <= 65535:
            parser.error('Port must be between 1 and 65535')
        uvicorn.run(create_app(Project(args.project)), host='127.0.0.1', port=args.port, limit_concurrency=16)
    else:
        report = evaluate(Project(args.project), args.partition, args.allow_assistant)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({key: value for key, value in report.items() if key != 'per_image'}, indent=2))


if __name__ == '__main__':
    main()
