from datetime import datetime
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel

from app.models.haproxy import K8sPodState


class RolloutStatus(str, Enum):
    """Статусы Argo Rollout"""
    HEALTHY = "Healthy"
    PROGRESSING = "Progressing"
    PAUSED = "Paused"
    DEGRADED = "Degraded"


class ReplicaSetInfo(BaseModel):
    """Информация о ReplicaSet (версия)"""
    name: str
    revision: int
    replicas: int
    available_replicas: int
    image: str
    created_at: datetime


class CanaryStep(BaseModel):
    """Шаг canary стратегии"""
    step_index: int
    set_weight: Optional[int] = None
    pause_duration: Optional[str] = None
    analysis: Optional[Dict[str, Any]] = None


class AnalysisRun(BaseModel):
    """Analysis run информация"""
    name: str
    status: str
    message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class RolloutInfo(BaseModel):
    """Основная информация о Rollout"""
    name: str
    namespace: str
    status: RolloutStatus
    strategy: str  # "Canary" | "BlueGreen"
    current_replicas: int
    desired_replicas: int
    available_replicas: int
    current_revision: int
    current_image: str = ""  # Текущий образ из spec.template
    replicasets: List[ReplicaSetInfo]  # Текущий + 3 предыдущих
    canary_steps: Optional[List[CanaryStep]] = None
    current_step_index: Optional[int] = None
    analysis_runs: Optional[List[AnalysisRun]] = None
    message: Optional[str] = None  # Сообщение о статусе
    k8s_pods: Optional[List[K8sPodState]] = None


class DeploymentInfo(BaseModel):
    """Информация о Deployment без Rollout"""
    name: str
    namespace: str
    replicas: int
    available_replicas: int
    current_image: str = ""  # Текущий образ
    strategy: str
    created_at: datetime
    k8s_pods: Optional[List[K8sPodState]] = None


class ArgoRolloutList(BaseModel):
    """Список всех rollouts и deployments"""
    rollouts: List[RolloutInfo]
    deployments: List[DeploymentInfo]  # Без rollout


class RolloutOperationResponse(BaseModel):
    """Ответ на операцию с rollout"""
    success: bool
    message: str
    operation_id: Optional[str] = None
