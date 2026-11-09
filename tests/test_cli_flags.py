from redline.cli import build_parser

RUN = ["run", "--data", "x", "--judge", "replay"]


def test_run_accepts_every_router_flag():
    flags = ["--router", "kmeans", "--k", "4", "--short-circuit", "--purity", "0.9"]
    flags += ["--min-members", "3", "--margin", "0.2"]
    args = build_parser().parse_args(RUN + flags)
    assert args.router == "kmeans"
    assert args.k == 4
    assert args.short_circuit is True
    assert args.purity == 0.9
    assert args.min_members == 3
    assert args.margin == 0.2


def test_run_defaults_and_compare_parse():
    parser = build_parser()
    args = parser.parse_args(RUN)
    assert (args.router, args.k, args.short_circuit) == ("center", 12, False)
    assert (args.purity, args.min_members, args.margin) == (1.0, 5, 0.10)
    args = parser.parse_args(["compare", "--runs", "a", "b", "--out", "c"])
    assert args.runs == ["a", "b"]
    assert args.out == "c"


def test_run_accepts_top_k_and_floor():
    args = build_parser().parse_args(RUN + ["--top-k", "3", "--floor", "0.4"])
    assert (args.top_k, args.floor) == (3, 0.4)
    args = build_parser().parse_args(RUN)
    assert (args.top_k, args.floor) == (1, None)


def test_run_accepts_an_embedder():
    assert build_parser().parse_args(RUN).embedder == "minilm"
    assert build_parser().parse_args(RUN + ["--embedder", "tfidf"]).embedder == "tfidf"
