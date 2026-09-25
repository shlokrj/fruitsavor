"""Start the single-user API with conservative local defaults."""
import argparse
import uvicorn

from .config import Settings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('Port must be between 1 and 65535')
    settings = Settings.from_env()
    if args.host not in {'127.0.0.1', '::1', 'localhost'} and not settings.api_token:
        parser.error('Set FRUITSAVOR_API_TOKEN before listening beyond localhost')
    uvicorn.run('fruitsavor.backend.app:create_app', factory=True, host=args.host, port=args.port,
                limit_concurrency=32, timeout_keep_alive=5, workers=1)


if __name__ == '__main__':
    main()
