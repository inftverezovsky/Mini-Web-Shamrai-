import bridge from '@vkontakte/vk-bridge';
import { apiFetch } from './api';

export interface VkDeliveryStatus {
  vk_user_id: string | null;
  group_id: number | null;
  configured: boolean;
  group_member: boolean;
  messages_allowed: boolean;
  notifications_allowed: boolean;
}

type BridgeSend = (method: string, params?: Record<string, unknown>) => Promise<any>;

export type VkConsentStepId = 'group' | 'messages' | 'notifications';

export interface VkGroupInfo {
  id?: number;
  name?: string;
  screen_name?: string;
  photo_50?: string;
  photo_100?: string;
  photo_200?: string;
}

export interface VkConsentStepResult {
  step: VkConsentStepId;
  ok: boolean;
  status: VkDeliveryStatus;
  error?: string;
}

const vkBridge = bridge as unknown as {
  send: BridgeSend;
  isWebView?: () => boolean;
};

let bridgeInitPromise: Promise<boolean> | null = null;

function hasVkLaunchParams(): boolean {
  if (typeof window === 'undefined') return false;
  return /(?:^|[?&])vk_(?:app_id|platform|user_id)=/i.test(window.location.search);
}

export function getVkGroupId(fallback?: number | null): number | null {
  const rawGroupId = import.meta.env.VITE_VK_GROUP_ID || '';
  const parsed = Number(rawGroupId || fallback || 0);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

export function getVkCommunityUrl(groupId?: number | null): string | null {
  const resolvedGroupId = getVkGroupId(groupId);
  return resolvedGroupId ? `https://vk.com/club${resolvedGroupId}` : null;
}

export function getVkMessagesUrl(groupId?: number | null): string | null {
  const resolvedGroupId = getVkGroupId(groupId);
  return resolvedGroupId ? `https://vk.me/club${resolvedGroupId}` : null;
}

export function getVkMiniAppUrl(): string | null {
  const rawAppId = import.meta.env.VITE_VK_ID_APP_ID || '';
  const parsed = Number(rawAppId || 0);
  return Number.isFinite(parsed) && parsed > 0 ? `https://vk.com/app${parsed}` : null;
}

export function isVkMiniAppRuntime(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    if (typeof vkBridge.isWebView === 'function' && vkBridge.isWebView()) return true;
  } catch {
    // Fall back to URL markers below.
  }
  return hasVkLaunchParams();
}

export async function ensureVkBridgeReady(): Promise<boolean> {
  if (typeof window === 'undefined') return false;
  if (!isVkMiniAppRuntime()) return false;
  if (!bridgeInitPromise) {
    bridgeInitPromise = vkBridge
      .send('VKWebAppInit', {})
      .then(() => true)
      .catch(() => false);
  }
  return bridgeInitPromise;
}

export async function detectVkMiniAppRuntime(): Promise<boolean> {
  if (!isVkMiniAppRuntime()) return false;
  return ensureVkBridgeReady();
}

export async function fetchVkDeliveryStatus(): Promise<VkDeliveryStatus> {
  return apiFetch<VkDeliveryStatus>('/users/me/vk-delivery-status');
}

async function persistVkDeliveryStatus(payload: {
  group_member?: boolean;
  messages_allowed?: boolean;
  notifications_allowed?: boolean;
}): Promise<VkDeliveryStatus> {
  return apiFetch<VkDeliveryStatus>('/users/me/vk-delivery-status', {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

function vkErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === 'object' && error && 'error_data' in error) {
    const errorData = (error as { error_data?: { error_reason?: string; error_description?: string } }).error_data;
    return errorData?.error_description || errorData?.error_reason || 'VK отменил запрос.';
  }
  return 'VK отменил запрос.';
}

export async function getVkGroupInfo(groupId?: number | null): Promise<VkGroupInfo | null> {
  const resolvedGroupId = getVkGroupId(groupId);
  if (!resolvedGroupId || !(await ensureVkBridgeReady())) return null;
  try {
    return await vkBridge.send('VKWebAppGetGroupInfo', { group_id: resolvedGroupId });
  } catch {
    return null;
  }
}

export async function joinVkGroup(groupId?: number | null): Promise<VkDeliveryStatus> {
  const resolvedGroupId = getVkGroupId(groupId);
  if (!resolvedGroupId) throw new Error('VK-сообщество не настроено для этой сборки.');
  if (!(await ensureVkBridgeReady())) {
    throw new Error('Вступить в сообщество можно только внутри VK Mini App.');
  }

  await vkBridge.send('VKWebAppJoinGroup', { group_id: resolvedGroupId });
  return persistVkDeliveryStatus({ group_member: true });
}

export async function requestVkMessagesPermission(groupId?: number | null): Promise<VkDeliveryStatus> {
  const resolvedGroupId = getVkGroupId(groupId);
  if (!resolvedGroupId) throw new Error('VK-сообщество не настроено для этой сборки.');
  if (!(await ensureVkBridgeReady())) {
    throw new Error('Разрешение сообщений можно запросить только внутри VK Mini App.');
  }

  await vkBridge.send('VKWebAppAllowMessagesFromGroup', { group_id: resolvedGroupId });
  return persistVkDeliveryStatus({ messages_allowed: true });
}

export async function requestVkNotificationsPermission(): Promise<VkDeliveryStatus> {
  if (!(await ensureVkBridgeReady())) {
    throw new Error('VK-уведомления можно включить только внутри VK Mini App.');
  }

  await vkBridge.send('VKWebAppAllowNotifications', {});
  return persistVkDeliveryStatus({ notifications_allowed: true });
}

export async function runVkConsentWizard(
  initialStatus: VkDeliveryStatus,
  options: { includeGroup?: boolean; includeNotifications?: boolean } = {},
): Promise<VkConsentStepResult[]> {
  const results: VkConsentStepResult[] = [];
  let status = initialStatus;

  const runStep = async (
    step: VkConsentStepId,
    enabled: boolean,
    action: () => Promise<VkDeliveryStatus>,
  ) => {
    if (!enabled) return;
    try {
      status = await action();
      results.push({ step, ok: true, status });
    } catch (error) {
      results.push({ step, ok: false, status, error: vkErrorMessage(error) });
    }
  };

  await runStep(
    'group',
    Boolean(options.includeGroup && !status.group_member),
    () => joinVkGroup(status.group_id),
  );
  await runStep('messages', !status.messages_allowed, () => requestVkMessagesPermission(status.group_id));
  await runStep(
    'notifications',
    Boolean(options.includeNotifications && !status.notifications_allowed),
    () => requestVkNotificationsPermission(),
  );

  return results;
}
