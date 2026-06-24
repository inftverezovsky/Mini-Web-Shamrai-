import { apiFetch } from './api';

export interface ConnectionOnboardingResponse {
  status: string;
  checklist: Record<string, boolean>;
  created_signal_types: string[];
}

export async function syncConnectionOnboarding(): Promise<ConnectionOnboardingResponse> {
  return apiFetch<ConnectionOnboardingResponse>('/signals/connection-onboarding/sync', {
    method: 'POST',
  });
}
