"""Portable supported M3 artifacts for shared registry/HTTP/PDF regression tests."""
from pathlib import Path
from hashlib import sha256
import json
from app.services.bo.m3_rebuild.sources import FilingSources
from tests.m3_rebuild.test_management import assess_management
from app.services.bo.m3_rebuild.structure import convert_structure, structure_filename
from app.services.bo.m3_rebuild.workflow import diagnostics,write_json
from tests.m3_rebuild.responses import with_credit_response

CASE='HCA_registry_fixture'
RUN='run_fixture'
MANAGEMENT_ID='m3.rebuild.management.'+RUN
STRUCTURE_ID='m3.rebuild.structure.'+RUN


def write_fixture(root):
    base=Path(__file__).resolve().parents[1]/'examples/m3_rebuild'
    directory=Path(root)/'m3_rebuild'/CASE/RUN;directory.mkdir(parents=True,exist_ok=True)
    sources=FilingSources(**json.loads((base/'hca_2024_sources.json').read_text(encoding="utf-8")))
    artifacts={'structure':convert_structure(json.loads((base/'hca_input.json').read_text(encoding="utf-8"))),
               'management':assess_management(with_credit_response(
                   json.loads((base/'hca_2024_extraction.json').read_text(encoding="utf-8")),
                   recent_names=['Jon M. Foster','Michael A. Marks','Michael R. McAlevey']),sources)}
    components={}
    for kind,artifact in artifacts.items():
        name=structure_filename(artifact) if kind=='structure' else 'Senior Management Assessment.json'
        write_json(directory/name,artifact);write_json(directory/(kind+'_diagnostics.json'),diagnostics())
        components[kind]={'status':artifact['completion_status'],'artifact':name,'schema_version':artifact['schema_version'],
                          'validation_status':'passed','artifact_sha256':sha256((directory/name).read_bytes()).hexdigest(),
                          'diagnostics':kind+'_diagnostics.json'}
    write_json(directory/'m3_rebuild_manifest.json',dict(schema_version='m3-rebuild-manifest-1.0',case_id=CASE,run_id=RUN,workflow='m3-rebuild',components=components))
    return directory
