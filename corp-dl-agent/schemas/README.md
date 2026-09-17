# schemas

pydantic strict 모델에서 내보낸 JSON schema. `python -m corp_dl_agent config validate` 등에서 사용하는 모델과 동일합니다.
- config.schema.json (AppConfig), taskspec.schema.json, cad_snapshot.schema.json, report_payload.schema.json, release_manifest.schema.json
재생성: `.venv/bin/python -c "from corp_dl_agent.reporting.schemas_export import export_all; export_all('schemas')"`
