from summarize_worker import skip_summarization, build_summarize_prompt, parse_observations


def _event(tool_name, tool_input="{}"):
    return {"id": 1, "tool_name": tool_name, "tool_input": tool_input, "project": "/proj"}


def test_skip_summarization_empty_list():
    assert skip_summarization([]) is True


def test_skip_summarization_below_threshold():
    events = [_event("Edit"), _event("Edit")]
    assert skip_summarization(events) is True


def test_skip_summarization_at_threshold():
    events = [_event("Edit"), _event("Edit"), _event("Write")]
    assert skip_summarization(events) is False


def test_skip_summarization_ignores_read_only_tools():
    events = [_event("Read"), _event("Grep"), _event("WebSearch"), _event("Edit"), _event("Edit")]
    assert skip_summarization(events) is True  # only 2 substantive


def test_build_summarize_prompt_includes_all_events():
    events = [_event("Edit", '{"file_path": "a.py"}'), _event("Bash", '{"command": "ls"}')]
    prompt = build_summarize_prompt(events)
    assert "JSON array" in prompt
    assert "Edit" in prompt
    assert "Bash" in prompt
    assert "a.py" in prompt


def test_parse_observations_valid_json():
    response = '[{"category": "bugfix", "summary": "Fixed X", "related_files": ["a.py"]}]'
    result = parse_observations(response)
    assert result == [{"category": "bugfix", "summary": "Fixed X", "related_files": ["a.py"]}]


def test_parse_observations_strips_markdown_fences():
    response = '```json\n[{"category": "feature", "summary": "Added Y", "related_files": []}]\n```'
    result = parse_observations(response)
    assert len(result) == 1
    assert result[0]["summary"] == "Added Y"


def test_parse_observations_invalid_category_defaults_to_change():
    response = '[{"category": "not-a-real-category", "summary": "Did something", "related_files": []}]'
    result = parse_observations(response)
    assert result[0]["category"] == "change"


def test_parse_observations_malformed_json_returns_empty():
    assert parse_observations("not json at all") == []


def test_parse_observations_empty_array():
    assert parse_observations("[]") == []


def test_parse_observations_non_list_returns_empty():
    assert parse_observations('{"not": "a list"}') == []


def test_parse_observations_skips_items_missing_summary():
    response = '[{"category": "bugfix", "related_files": []}]'
    assert parse_observations(response) == []
