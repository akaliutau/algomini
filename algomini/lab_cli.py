"""CLI adapters for the reusable laboratory; APIs return JSON-compatible values."""

from pathlib import Path

from . import campaigns, labtools, memory, semantic


COMMANDS = {"lab", "memory", "campaign", "text-e2e"}


def configure(commands):
    integration = commands.add_parser("text-e2e", help="Run/replay isolated Wikipedia-prefix package integration")
    integration.add_argument("--output", help="A separate report directory for a new integration run")
    lab = commands.add_parser("lab", help="Discover/run bounded deterministic research probes")
    actions = lab.add_subparsers(dest="action", required=True)
    actions.add_parser("list")
    probe = actions.add_parser("probe")
    probe.add_argument("probe_id")
    probe.add_argument("input")
    probe.add_argument("--family", required=True, choices=sorted(memory.FAMILIES))
    retrieval = commands.add_parser("memory", help="Incrementally index, search or export shared knowledge")
    retrieval.add_argument("action", choices=("index", "search", "export"))
    retrieval.add_argument("query", nargs="?")
    retrieval.add_argument("--family", choices=sorted(memory.FAMILIES))
    retrieval.add_argument("--semantic", action="store_true", help="Require the optional pinned local embedding model")
    campaign = commands.add_parser("campaign", help="Create/prepare/export an isolated research workspace")
    actions = campaign.add_subparsers(dest="action", required=True)
    create = actions.add_parser("init")
    create.add_argument("workspace")
    create.add_argument("sample")
    create.add_argument("--id", required=True)
    create.add_argument("--family", required=True, choices=sorted(memory.FAMILIES))
    create.add_argument("--probe-bytes", type=int, default=65536)
    actions.add_parser("prepare").add_argument("workspace")
    export = actions.add_parser("export")
    export.add_argument("workspace")
    export.add_argument("destination")


def run(args, root, cfg):
    if args.command == "text-e2e":
        from .text_e2e import run as integration
        result = integration(root, cfg, output=args.output)
        return {key: result[key] for key in ("ok", "input_bytes", "input_sha256", "archive_bytes", "best_baseline", "archive_gain", "package_id", "paid_model_calls", "docker_used", "scope")}
    if args.command == "lab":
        if args.action == "list":
            return {"probes": labtools.catalog()}
        with Path(args.input).open("rb") as stream:
            data = stream.read(labtools.MAX_INPUT + 1)
        return labtools.run(args.probe_id, data, family=args.family)
    if args.command == "memory":
        embedder = semantic.load(root, required=args.semantic)
        if args.action == "search":
            if not args.query:
                raise ValueError("Memory search requires a query")
            return {"matches": memory.search(root, args.query, family=args.family, embedder=embedder)}
        if args.action == "export":
            return memory.export(root, embedder=embedder)
        return memory.rebuild(root, embedder=embedder)
    if args.action == "init":
        campaign = campaigns.Campaign.create(args.workspace, root, args.sample, campaign_id=args.id,
                                              family=args.family, probe_bytes=args.probe_bytes)
        return {"ok": True, "workspace": str(campaign.workspace), "campaign": campaign.manifest()}
    campaign = campaigns.Campaign(Path(args.workspace).resolve(), root)
    return campaign.prepare() if args.action == "prepare" else campaign.export(args.destination)
