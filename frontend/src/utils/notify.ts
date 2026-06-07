export type NoticeType = 'success' | 'error' | 'warning' | 'info' | 'pending';

export interface NoticePayload {
  id?: string;
  type: NoticeType;
  title?: string;
  message: string;
  duration?: number;
}

export interface ConfirmPayload {
  id: string;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: 'danger' | 'warning';
  resolve: (confirmed: boolean) => void;
}

type NotifyListener = (payload: NoticePayload) => void;
type ConfirmListener = (payload: ConfirmPayload) => void;

const noticeListeners = new Set<NotifyListener>();
const confirmListeners = new Set<ConfirmListener>();

export function subscribeNotice(listener: NotifyListener) {
  noticeListeners.add(listener);
  return () => noticeListeners.delete(listener);
}

export function subscribeConfirm(listener: ConfirmListener) {
  confirmListeners.add(listener);
  return () => confirmListeners.delete(listener);
}

export function notify(payload: NoticePayload) {
  noticeListeners.forEach((listener) => listener(payload));
}

export function notifySuccess(message: string, title = 'Готово') {
  notify({ type: 'success', title, message });
}

export function notifyError(message: string, title = 'Ошибка') {
  notify({ type: 'error', title, message, duration: 5600 });
}

export function notifyInfo(message: string, title = 'Статус') {
  notify({ type: 'info', title, message });
}

export function notifyPending(message: string, title = 'Платеж в обработке') {
  notify({ type: 'pending', title, message, duration: 7000 });
}

export function confirmDestructive({
  title = 'Подтвердите действие',
  message,
  confirmLabel = 'Подтвердить',
  cancelLabel = 'Отмена',
  tone = 'danger',
}: Omit<ConfirmPayload, 'id' | 'resolve'>): Promise<boolean> {
  return new Promise((resolve) => {
    const payload: ConfirmPayload = {
      id: crypto.randomUUID(),
      title,
      message,
      confirmLabel,
      cancelLabel,
      tone,
      resolve,
    };
    confirmListeners.forEach((listener) => listener(payload));
  });
}
