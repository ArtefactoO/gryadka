// Argo Rollout Types

export enum RolloutStatus {
  HEALTHY = 'Healthy',
  PROGRESSING = 'Progressing',
  PAUSED = 'Paused',
  DEGRADED = 'Degraded',
}

export interface ReplicaSetInfo {
  name: string
  revision: number
  replicas: number
  available_replicas: number
  image: string
  created_at: string
}

export interface CanaryStep {
  step_index: number
  set_weight?: number
  pause_duration?: string
  analysis?: Record<string, any>
}

export interface AnalysisRun {
  name: string
  status: string
  message?: string
  started_at?: string
  completed_at?: string
}

export interface ArgoRolloutPodState {
  name?: string
  uid?: string
  phase?: string
  status_detail?: string
  ready: boolean
  terminating: boolean
  restarts?: number
  start_time?: string
}

export interface RolloutInfo {
  name: string
  namespace: string
  status: RolloutStatus
  strategy: 'Canary' | 'BlueGreen'
  current_replicas: number
  desired_replicas: number
  available_replicas: number
  current_revision: number
  current_image: string
  replicasets: ReplicaSetInfo[]
  canary_steps?: CanaryStep[]
  current_step_index?: number
  analysis_runs?: AnalysisRun[]
  message?: string
  k8s_pods?: ArgoRolloutPodState[]
}

export interface DeploymentInfo {
  name: string
  namespace: string
  replicas: number
  available_replicas: number
  current_image: string
  strategy: string
  created_at: string
  k8s_pods?: ArgoRolloutPodState[]
}

export interface ArgoRolloutList {
  rollouts: RolloutInfo[]
  deployments: DeploymentInfo[]
}

export interface RolloutOperationResponse {
  success: boolean
  message: string
  operation_id?: string
}
