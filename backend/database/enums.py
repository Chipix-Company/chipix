from enum import Enum


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    QUEUED = "queued"
    IDLE = "idle"


class VerificationType(str, Enum):
    UNITSIM = "unitsim"
    FORMAL = "formal"
    UVM = "uvm"
    ALL = "all"


class ArtifactType(str, Enum):
    SPEC = "spec"
    RTL = "rtl"
    GENERATED = "generated"
