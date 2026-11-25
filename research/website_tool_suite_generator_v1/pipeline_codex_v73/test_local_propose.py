from pathlib import Path

from PIL import Image

from local_propose import _compact_recon, _load_bounded_images, _prompt, _select_image_paths


def test_proposals_request_unambiguous_list_contracts() -> None:
    prompt = _prompt({"start_url": "https://example.test", "allowed_host": "example.test", "pages": []})
    assert "what counts as one item" in prompt
    assert "whether to include nested" in prompt
    assert "empty or short list" in prompt
    assert "simplify the output or omit the" in prompt


def test_proposal_prompt_distinguishes_list_fields_and_typed_defaults() -> None:
    prompt = _prompt({"start_url": "https://example.test", "allowed_host": "example.test", "pages": []})
    assert '{"items": "list[object]"}' in prompt
    assert 'NOT a schema type such as list[object]' in prompt
    assert 'unquoted number' in prompt
    assert 'Copy every argument\'s default exactly' in prompt
    assert 'not merely listing menus, field options' in prompt
    assert 'Never claim personalized eligibility' in prompt


def test_image_sampling_spans_the_full_crawl(tmp_path: Path) -> None:
    recon_dir = tmp_path / "recon"
    screenshots = recon_dir / "screenshots"
    screenshots.mkdir(parents=True)
    for index in range(12):
        (screenshots / f"page_{index:02d}.png").write_bytes(b"png")
    selected = _select_image_paths(recon_dir / "recon.json", 4)
    assert [path.name for path in selected] == [
        "page_00.png",
        "page_04.png",
        "page_07.png",
        "page_11.png",
    ]


def test_image_sampling_keeps_all_when_under_limit(tmp_path: Path) -> None:
    recon_dir = tmp_path / "recon"
    screenshots = recon_dir / "screenshots"
    screenshots.mkdir(parents=True)
    for index in range(3):
        (screenshots / f"page_{index:02d}.png").write_bytes(b"png")
    assert len(_select_image_paths(recon_dir / "recon.json", 4)) == 3


def test_loaded_images_obey_pixel_budget(tmp_path: Path) -> None:
    path = tmp_path / "large.png"
    Image.new("RGB", (1280, 1800), "white").save(path)
    image = _load_bounded_images([path], 500_000)[0]
    assert image.width * image.height <= 500_000
    assert abs((image.width / image.height) - (1280 / 1800)) < 0.01


def test_compact_recon_bounds_each_surface() -> None:
    records = [{"text": str(index), "href": f"/{index}"} for index in range(100)]
    recon = {
        "start_url": "https://example.test",
        "allowed_host": "example.test",
        "pages": [{
            "final_url": "https://example.test/items",
            "title": "Items",
            "http_status": 200,
            "frames": [{
                "url": "https://example.test/items",
                "headings": records,
                "links": records,
                "actions": records,
                "fields": records,
                "forms": records,
                "content": records,
            }],
        }],
        "repeated_first_party_routes": records,
    }
    compact = _compact_recon(recon)
    surface = compact["pages"][0]["surfaces"][0]
    assert len(surface["headings"]) == 8
    assert len(surface["other_links"]) == 12
    assert len(surface["actions"]) == 8
    assert len(surface["fields"]) == 8
    assert len(surface["forms"]) == 3
    assert len(surface["content_samples"]) == 24
    assert surface["headings"][0]["evidence_id"] == "p00.s00.heading.000"
    assert surface["content_samples"][0]["evidence_id"] == "p00.s00.content.000"
    assert len(compact["repeated_routes"]) == 20
