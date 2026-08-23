from database.enums import ArtifactType, RunStatus, VerificationType


def test_run_status_string_compatibility():
    assert RunStatus.COMPLETED == "completed"
    assert RunStatus.FAILED == "failed"
    assert RunStatus.RUNNING == "running"
    assert RunStatus.CANCELLED == "cancelled"
    assert RunStatus.INTERRUPTED == "interrupted"


def test_verification_type_string_compatibility():
    assert VerificationType.UNITSIM == "unitsim"
    assert VerificationType.FORMAL == "formal"
    assert VerificationType.UVM == "uvm"
    assert VerificationType.ALL == "all"


def test_artifact_type_string_compatibility():
    assert ArtifactType.SPEC == "spec"
    assert ArtifactType.RTL == "rtl"
    assert ArtifactType.GENERATED == "generated"
