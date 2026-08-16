export const GENESIS_HASH: string;

export interface TraceBuilderOptions {
  traceId: string;
  agentId: string;
  agentOwner: string;
  submittedBy: string;
  taskDescription: string;
  timestamp: Date | string;
  domainProfile: string;
  hmacKey: string | Buffer;
  escalationEvents?: string[];
  errorEvents?: string[];
  callbackUrl?: string;
  /** "test" | "production". Defaults server-side to "production". */
  environment?: string;
  /** Free text, e.g. "langchain", "crewai", "otel", "custom". */
  framework?: string;
}

export interface StepFields {
  input: unknown;
  output: unknown;
  reasoning?: string;
}

export interface DecisionTrace {
  trace_id: string;
  agent_id: string;
  agent_owner: string;
  submitted_by: string;
  task_description: string;
  timestamp: string;
  domain_profile: string;
  final_output: string;
  escalation_events: string[];
  error_events: string[];
  callback_url?: string;
  environment?: string;
  framework?: string;
  steps: Array<{
    step_id: number;
    tool: string;
    input: unknown;
    output: unknown;
    reasoning?: string;
    step_hash: string;
  }>;
  trace_root_hash: string;
}

export class TraceBuilder {
  constructor(options: TraceBuilderOptions);
  addStep(stepId: number, tool: string, fields: StepFields): this;
  finalize(finalOutput: string): this;
  build(): DecisionTrace;
}

export interface ClientOptions {
  apiKey: string;
  baseUrl: string;
  timeoutMs?: number;
}

export interface EvaluateOptions {
  force?: boolean;
  runs?: number;
  mode?: 'evaluate' | 'calibrate';
}

export class JiminyAPIError extends Error {
  status: number;
  body: unknown;
}

export class Client {
  constructor(options: ClientOptions);
  evaluate(trace: DecisionTrace | Record<string, unknown>, options?: EvaluateOptions): Promise<Record<string, unknown>>;
}

export function canonical(obj: unknown): string;
