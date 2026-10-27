"""Reference round-trip, deterministic geometry and malformed-input acceptance tests."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from app.services.bo.m3_rebuild.structure import convert_structure, render_svg, validate_geometry, structure_filename

EXAMPLES = Path(__file__).resolve().parents[2] / "examples/m3_rebuild"


def fixture():
    return json.loads((EXAMPLES / "hca_input.json").read_text(encoding="utf-8"))


def flat(n):
    yield n
    for c in n.get("children", []):
        yield from flat(c)


def test_reference_contract_and_lossless_roundtrip():
    document = fixture()
    before = deepcopy(document)
    result = convert_structure(document)
    reference = json.loads((EXAMPLES / "hca_reference.json").read_text(encoding="utf-8"))
    assert document == before
    assert set(reference) <= set(result)
    assert result["visual_spec"] == reference["visual_spec"]
    c, ref = result["companies"]["HCA"], reference["companies"]["HCA"]
    assert len(c["nodes"]) == 14 and len(c["ownership_links"]) == 13
    assert sum(n["is_guarantor"] for n in c["nodes"]) == 5
    assert set(ref) <= set(c)
    assert {n["id"]:n["box"] for n in c["nodes"]} == {n["id"]:n["box"] for n in ref["nodes"]}
    assert c["plain_text_tree"] == ref["plain_text_tree"]
    by_id = {n["id"]:n for n in c["nodes"]}
    for n in flat(document["corporate_tree"]):
        out = by_id[n["id"]]
        assert (out["display_name"], out["role"], out["is_guarantor"]) == (n["name"], n["role"], n["is_guarantor"])
        assert out.get("record_type") == n.get("record_type")
        expected = [x["id"] for x in n.get("children", [])]
        actual = [x["child_id"] for x in c["ownership_links"] if x["parent_id"] == n["id"]]
        assert actual == expected
        for child in n.get("children", []):
            edge = next(e for e in c["ownership_links"] if e["child_id"] == child["id"])
            assert edge["ownership_percent"] == child["ownership_percent_from_parent"]
    assert structure_filename(result) == "Visualized HCA Healthcare Corporate structure.json"
    assert "SIMULATED EDUCATIONAL EXAMPLE" in result["simulation_disclaimer"]


def test_determinism_rendering_and_single_child_geometry():
    result = convert_structure(fixture())
    assert json.dumps(result) == json.dumps(convert_structure(fixture()))
    svg = render_svg(result)
    assert svg == render_svg(convert_structure(fixture()))
    assert svg.count('<polyline') == 13
    assert svg.count('l 5,5 l 10,-12') == 6  # five guarantors plus legend
    c = result["companies"]["HCA"]
    for path in c["connector_paths"]:
        children = [e for e in c["ownership_links"] if e["parent_id"] == path["parent_id"]]
        if len(children) == 1:
            assert len(path["points"]) == 2
            assert path["points"][0][0] == path["points"][1][0]
        else:
            assert len(path["points"]) == 4
    validate_geometry(result)


@pytest.mark.parametrize('bad',[-1,101,"100",True,None,float('nan'),float('inf')])
def test_invalid_percentages(bad):
    d=fixture();d["corporate_tree"]["children"][0]["ownership_percent_from_parent"]=bad
    with pytest.raises(ValueError,match='percentage'):
        convert_structure(d)


@pytest.mark.parametrize('mutation', ['duplicate','string_flag','missing_name','children_object','unknown_role','root_percent','missing_status','record_type_object'])
def test_invalid_trees(mutation):
    d=fixture(); root=d["corporate_tree"]; child=root["children"][0]
    if mutation=='duplicate': child['id']=root['id']
    if mutation=='string_flag': child['is_guarantor']='false'
    if mutation=='missing_name': del child['name']
    if mutation=='children_object': child['children']={}
    if mutation=='unknown_role': child['role']='Bank'
    if mutation=='root_percent': root['ownership_percent_from_parent']=100
    if mutation=='missing_status': del d['data_status']
    if mutation=='record_type_object': child['record_type']={}
    with pytest.raises(ValueError):convert_structure(d)


def test_expansion_policy_keeps_nodes_readable_and_subtree_buses_disjoint():
    d=fixture();d['company_name']='Large Example'
    root=d['corporate_tree'];root['id']='root'
    root['children']=[dict(id=f'p{i}',name=f'Parent {i}',role='HoldCo',is_guarantor=False,ownership_percent_from_parent=100,
                          children=[dict(id=f'n{i}_{j}',name=f'Operating Company {i} {j}',role='OpCo',is_guarantor=False,ownership_percent_from_parent=50) for j in range(5)]) for i in range(10)]
    r=convert_structure(d);validate_geometry(r)
    assert r['visual_spec']['canvas']['width_px']==13080
    assert all(n['box']['width_px']==220 for n in next(iter(r['companies'].values()))['nodes'])
    assert render_svg(r)
    assert structure_filename(r)=='Visualized Large Example Corporate structure.json'
    assert r==convert_structure(d)


def test_deep_canvas_expands_without_compression():
    d=fixture();root=d['corporate_tree'];root['children']=[]
    for i in range(12):
        child=dict(id=f'd{i}',name=f'Level {i}',role='OpCo',is_guarantor=False,ownership_percent_from_parent=100)
        root['children']=[child];root=child
    r=convert_structure(d)
    assert r['visual_spec']['canvas']['height_px']>=2900
    assert all(len(p['points'])==2 for p in next(iter(r['companies'].values()))['connector_paths'])


def test_unrelated_ownership_connector_crossings_are_rejected():
    r=convert_structure(fixture())
    # Two artificial orthogonal crossing segments, away from any node.
    c=r['companies']['HCA']
    c['connector_paths']=[{'parent_id':'first','points':[[10,140],[200,140]]},
                          {'parent_id':'second','points':[[100,130],[100,150]]}]
    with pytest.raises(ValueError,match='cross'):validate_geometry(r)
