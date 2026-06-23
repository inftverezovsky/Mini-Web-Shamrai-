export function formatApiErrorDetail(detail: unknown): string | null {
  if (!detail) return null;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === 'string') return item;
        if (item && typeof item === 'object' && 'msg' in item) return String((item as { msg: unknown }).msg);
        return null;
      })
      .filter(Boolean)
      .join('; ') || null;
  }
  if (typeof detail === 'object' && 'msg' in detail) return String((detail as { msg: unknown }).msg);
  return null;
}

export function formatApiErrorMessage(status: number, detail: unknown): string {
  const detailMessage = formatApiErrorDetail(detail);

  if (status === 429) {
    return detailMessage || 'Слишком много действий подряд. Подождите немного и повторите попытку.';
  }

  if (status === 401) {
    return detailMessage || 'Сессия устарела. Войдите снова.';
  }

  if (status === 403) {
    return detailMessage || 'Недостаточно прав для этого действия.';
  }

  if (status === 413) {
    return detailMessage || 'Запрос слишком большой. Уменьшите файл или данные и попробуйте еще раз.';
  }

  if (status === 502 || status === 503 || status === 504) {
    return detailMessage || 'Сервис временно недоступен. Попробуйте еще раз чуть позже.';
  }

  if (status >= 500) {
    return detailMessage || 'На сервере произошла ошибка. Попробуйте еще раз.';
  }

  return detailMessage || 'Не удалось выполнить запрос. Проверьте данные и попробуйте еще раз.';
}
