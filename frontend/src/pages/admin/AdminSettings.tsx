import React, { useEffect, useMemo, useRef, useState } from 'react';
import { apiFetch } from '../../utils/api';
import type { MessageTemplateResponse } from '../../schemas/schemas';
import { confirmDestructive, notifyError, notifySuccess } from '../../utils/notify';
import SmoothCollapse from '../../components/SmoothCollapse';
import {
  AlertTriangle,
  Braces,
  Check,
  ChevronDown,
  Clock3,
  Loader2,
  MessageCircle,
  MonitorSmartphone,
  RotateCcw,
  Save,
  Search,
  Send,
  Settings2,
  Sparkles,
  type LucideIcon,
} from 'lucide-react';

type ChannelGroupId = 'telegram' | 'vk' | 'site';
type SettingsSectionId = 'message_texts';

interface ChannelGroupConfig {
  id: ChannelGroupId;
  title: string;
  subtitle: string;
  badge: string;
  Icon: LucideIcon;
  templateKeys: string[];
}

interface SettingsSectionConfig {
  id: SettingsSectionId;
  title: string;
  subtitle: string;
  badge: string;
  Icon: LucideIcon;
}

type TemplateEditorToken =
  | { type: 'text'; value: string }
  | { type: 'variable'; raw: string; key: string; label: string; example: string }
  | { type: 'markup'; raw: string };

const SETTINGS_SECTIONS: SettingsSectionConfig[] = [
  {
    id: 'message_texts',
    title: 'Тексты сообщений',
    subtitle: 'Telegram, VK, сайт',
    badge: 'тексты',
    Icon: MessageCircle,
  },
];

const CHANNEL_GROUPS: ChannelGroupConfig[] = [
  {
    id: 'telegram',
    title: 'Telegram',
    subtitle: 'бот и личные уведомления',
    badge: 'TG',
    Icon: Send,
    templateKeys: [
      'telegram_welcome',
      'announcement',
      'paid_set_teaser',
      'forecast_teaser',
      'forecast_full',
      'odds_drop',
      'bet_win',
      'bet_loss',
      'bet_loss_supercompensation',
      'bet_refund',
      'live_signal',
    ],
  },
  {
    id: 'vk',
    title: 'VK',
    subtitle: 'сообщения сообщества',
    badge: 'VK',
    Icon: MessageCircle,
    templateKeys: [
      'announcement',
      'paid_set_teaser',
      'forecast_teaser',
      'forecast_full',
      'odds_drop',
      'bet_win',
      'bet_loss',
      'bet_loss_supercompensation',
      'bet_refund',
      'live_signal',
    ],
  },
  {
    id: 'site',
    title: 'Сайт',
    subtitle: 'чат и web-уведомления',
    badge: 'WEB',
    Icon: MonitorSmartphone,
    templateKeys: [
      'announcement',
      'paid_set_teaser',
      'forecast_teaser',
      'forecast_full',
      'odds_drop',
      'bet_win',
      'bet_loss',
      'bet_loss_supercompensation',
      'bet_refund',
      'live_signal',
    ],
  },
];

const GROUP_TONE: Record<ChannelGroupId, {
  header: string;
  marker: string;
  activeCard: string;
}> = {
  telegram: {
    header: 'border-cyan-300/20 bg-cyan-300/[0.075] text-cyan-100',
    marker: 'border-cyan-300/25 bg-cyan-300/15 text-cyan-100',
    activeCard: 'border-cyan-300/45 bg-cyan-300/[0.14] shadow-neon-cyan',
  },
  vk: {
    header: 'border-[#4f8cff]/25 bg-[#4f8cff]/[0.095] text-blue-100',
    marker: 'border-[#4f8cff]/30 bg-[#4f8cff]/15 text-blue-100',
    activeCard: 'border-[#4f8cff]/45 bg-[#4f8cff]/[0.14] shadow-[0_0_22px_rgba(79,140,255,0.13)]',
  },
  site: {
    header: 'border-emerald-300/20 bg-emerald-300/[0.075] text-emerald-100',
    marker: 'border-emerald-300/25 bg-emerald-300/15 text-emerald-100',
    activeCard: 'border-emerald-300/45 bg-emerald-300/[0.14] shadow-[0_0_22px_rgba(52,211,153,0.13)]',
  },
};

function formatTemplateDate(value: string | null) {
  if (!value) return 'не меняли';
  return new Date(value).toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function htmlToPlainText(value: string) {
  const withLineBreaks = value
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/(?:p|div|li|h[1-6])>/gi, '\n');
  return withLineBreaks
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function buildPreview(template: MessageTemplateResponse | null, body: string) {
  if (!template) return '';
  const examples = new Map(template.variables.map(variable => [variable.key, variable.example || variable.label]));
  const rendered = body.replace(/{{\s*([a-zA-Z0-9_]+)\s*}}/g, (_match, key: string) => {
    return examples.get(key) || '';
  });
  return htmlToPlainText(rendered);
}

function sanitizeEditableText(value: string) {
  return value.replace(/[{}<>]/g, '');
}

function parseTemplateEditorTokens(
  template: MessageTemplateResponse | null,
  body: string,
): TemplateEditorToken[] {
  const variableMeta = new Map(
    (template?.variables || []).map(variable => [variable.key, variable]),
  );
  const tokens: TemplateEditorToken[] = [];
  const tokenPattern = /({{\s*([a-zA-Z0-9_]+)\s*}}|<\/?[^>]+>)/g;
  let cursor = 0;
  let match: RegExpExecArray | null;

  const pushText = (value: string) => {
    if (!value) return;
    tokens.push({ type: 'text', value });
  };

  while ((match = tokenPattern.exec(body)) !== null) {
    pushText(body.slice(cursor, match.index));
    const raw = match[0];
    const variableKey = match[2];
    if (variableKey) {
      const meta = variableMeta.get(variableKey);
      tokens.push({
        type: 'variable',
        raw,
        key: variableKey,
        label: meta?.label || variableKey,
        example: meta?.example || '',
      });
    } else {
      tokens.push({ type: 'markup', raw });
    }
    cursor = match.index + raw.length;
  }

  pushText(body.slice(cursor));
  return tokens;
}

function serializeTemplateEditorTokens(tokens: TemplateEditorToken[]) {
  return tokens.map(token => {
    if (token.type === 'text') return token.value;
    return token.raw;
  }).join('');
}

function rowsForEditableText(value: string) {
  const lineCount = value.split('\n').length;
  const lengthRows = Math.ceil(value.length / 58);
  return Math.min(9, Math.max(2, lineCount, lengthRows));
}

export default function AdminSettings() {
  const [templates, setTemplates] = useState<MessageTemplateResponse[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [activeKey, setActiveKey] = useState<string>('');
  const [activeSettingsSection, setActiveSettingsSection] = useState<SettingsSectionId>('message_texts');
  const [searchTerm, setSearchTerm] = useState('');
  const [expandedGroups, setExpandedGroups] = useState<Record<ChannelGroupId, boolean>>({
    telegram: false,
    vk: false,
    site: false,
  });
  const editorPanelRef = useRef<HTMLElement | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [resettingKey, setResettingKey] = useState<string | null>(null);

  const loadTemplates = async () => {
    try {
      setLoading(true);
      setLoadError(null);
      const data = await apiFetch<MessageTemplateResponse[]>('/admin/message-templates');
      setTemplates(data);
      setDrafts(data.reduce<Record<string, string>>((acc, template) => {
        acc[template.key] = template.body;
        return acc;
      }, {}));
      setActiveKey(current => current || data[0]?.key || '');
    } catch (err: any) {
      setLoadError(err.message || 'Не удалось загрузить шаблоны');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadTemplates();
  }, []);

  const templateByKey = useMemo(() => (
    new Map(templates.map(template => [template.key, template]))
  ), [templates]);

  const groupedTemplates = useMemo(() => {
    const query = searchTerm.trim().toLowerCase();
    return CHANNEL_GROUPS.map(group => {
      const groupTemplates = group.templateKeys
        .map(templateKey => templateByKey.get(templateKey))
        .filter((template): template is MessageTemplateResponse => Boolean(template));

      const visibleTemplates = query
        ? groupTemplates.filter(template => (
          template.title.toLowerCase().includes(query)
          || template.description.toLowerCase().includes(query)
          || template.key.toLowerCase().includes(query)
        ))
        : groupTemplates;

      return {
        ...group,
        templates: visibleTemplates,
        total: groupTemplates.length,
      };
    });
  }, [searchTerm, templateByKey]);

  const activeTemplate = useMemo(() => (
    templates.find(template => template.key === activeKey) || templates[0] || null
  ), [activeKey, templates]);
  const customTemplateCount = useMemo(() => (
    templates.filter(template => template.is_custom).length
  ), [templates]);
  const dirtyTemplateCount = useMemo(() => (
    templates.filter(template => (drafts[template.key] ?? template.body) !== template.body).length
  ), [drafts, templates]);
  const activeBody = activeTemplate ? drafts[activeTemplate.key] ?? activeTemplate.body : '';
  const activePreview = useMemo(() => buildPreview(activeTemplate, activeBody), [activeBody, activeTemplate]);
  const editorTokens = useMemo(
    () => parseTemplateEditorTokens(activeTemplate, activeBody),
    [activeBody, activeTemplate],
  );
  const isDirty = Boolean(activeTemplate && activeBody !== activeTemplate.body);
  const busy = Boolean(activeTemplate && (savingKey === activeTemplate.key || resettingKey === activeTemplate.key));

  const setActiveBody = (value: string) => {
    if (!activeTemplate) return;
    setDrafts(current => ({ ...current, [activeTemplate.key]: value }));
  };

  const toggleGroup = (groupId: ChannelGroupId) => {
    setExpandedGroups(current => ({ ...current, [groupId]: !current[groupId] }));
  };

  const handleSelectTemplate = (templateKey: string) => {
    setActiveKey(templateKey);
    if (typeof window === 'undefined' || !window.matchMedia('(max-width: 1023px)').matches) return;

    window.requestAnimationFrame(() => {
      editorPanelRef.current?.scrollIntoView({
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
        block: 'start',
      });
    });
  };

  const handleTextTokenChange = (tokenIndex: number, value: string) => {
    if (!activeTemplate) return;
    const nextTokens = parseTemplateEditorTokens(activeTemplate, activeBody).map((token, index) => (
      index === tokenIndex && token.type === 'text'
        ? { ...token, value: sanitizeEditableText(value) }
        : token
    ));
    setActiveBody(serializeTemplateEditorTokens(nextTokens));
  };

  const replaceTemplateInState = (template: MessageTemplateResponse) => {
    setTemplates(current => current.map(item => (item.key === template.key ? template : item)));
    setDrafts(current => ({ ...current, [template.key]: template.body }));
  };

  const handleSave = async () => {
    if (!activeTemplate) return;
    if (!activeBody.trim()) {
      notifyError('Текст шаблона не может быть пустым');
      return;
    }
    try {
      setSavingKey(activeTemplate.key);
      const saved = await apiFetch<MessageTemplateResponse>(`/admin/message-templates/${encodeURIComponent(activeTemplate.key)}`, {
        method: 'PUT',
        body: JSON.stringify({ body: activeBody }),
      });
      replaceTemplateInState(saved);
      notifySuccess('Шаблон сохранен');
    } catch (err: any) {
      notifyError(err.message || 'Не удалось сохранить шаблон');
    } finally {
      setSavingKey(null);
    }
  };

  const renderTemplateActions = (
    template: MessageTemplateResponse,
    className: string,
  ) => (
    <div className={className}>
      <button
        type="button"
        onClick={handleReset}
        disabled={busy}
        className="flex min-h-[42px] items-center justify-center gap-2 rounded-2xl border border-white/10 bg-white/[0.04] px-3 text-[10px] font-black uppercase tracking-wider text-slate-300 transition-all hover:border-amber-300/35 hover:text-amber-100 active:scale-[0.99] disabled:opacity-50"
      >
        {resettingKey === template.key ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
        <span>Сброс</span>
      </button>
      <button
        type="button"
        onClick={handleSave}
        disabled={busy || !isDirty}
        className="flex min-h-[42px] items-center justify-center gap-2 rounded-2xl border border-emerald-300/25 bg-emerald-400/15 px-3 text-[10px] font-black uppercase tracking-wider text-emerald-100 transition-all hover:bg-emerald-400/20 active:scale-[0.99] disabled:opacity-50"
      >
        {savingKey === template.key ? <Loader2 className="h-4 w-4 animate-spin" /> : isDirty ? <Save className="h-4 w-4" /> : <Check className="h-4 w-4" />}
        <span>{isDirty ? 'Сохранить' : 'Сохранено'}</span>
      </button>
    </div>
  );

  const handleReset = async () => {
    if (!activeTemplate) return;
    const confirmed = await confirmDestructive({
      title: 'Сбросить шаблон',
      message: `Вернуть стандартный текст для «${activeTemplate.title}»?`,
      confirmLabel: 'Сбросить',
      tone: 'warning',
    });
    if (!confirmed) return;

    try {
      setResettingKey(activeTemplate.key);
      const reset = await apiFetch<MessageTemplateResponse>(`/admin/message-templates/${encodeURIComponent(activeTemplate.key)}/reset`, {
        method: 'POST',
      });
      replaceTemplateInState(reset);
      notifySuccess('Шаблон сброшен');
    } catch (err: any) {
      notifyError(err.message || 'Не удалось сбросить шаблон');
    } finally {
      setResettingKey(null);
    }
  };

  if (loading) {
    return (
      <div className="flex min-h-[44vh] items-center justify-center">
        <Loader2 className="h-7 w-7 animate-spin text-cyan-300" />
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="space-y-3 rounded-2xl border border-rose-500/25 bg-rose-500/10 p-5 text-center text-xs text-rose-100">
        <AlertTriangle className="mx-auto h-7 w-7 text-rose-300" />
        <p className="font-bold">{loadError}</p>
        <button
          type="button"
          onClick={() => void loadTemplates()}
          className="rounded-xl border border-rose-300/30 bg-rose-300/10 px-4 py-2 font-black uppercase tracking-wider text-rose-50"
        >
          Повторить
        </button>
      </div>
    );
  }

  return (
    <div className="min-w-0 space-y-4 pb-10 animate-slide-up sm:space-y-5">
      <div className="rounded-3xl border border-white/10 bg-slate-950/42 p-3 shadow-glass backdrop-blur-xl sm:p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 className="flex items-center text-lg font-black uppercase tracking-wider text-white">
            <Settings2 className="mr-2 h-5 w-5 text-cyan-300" />
            Настройки
            </h2>
            <p className="mt-0.5 text-[10px] font-bold uppercase tracking-widest text-slate-400">
              Разделы панели
            </p>
          </div>
          <div className="flex items-center gap-2 rounded-2xl border border-cyan-300/20 bg-cyan-300/10 px-3 py-2 text-[10px] font-black uppercase tracking-wider text-cyan-100">
            <Sparkles className="h-4 w-4 text-cyan-200" />
            <span>{customTemplateCount}/{templates.length} изменено</span>
          </div>
        </div>

        <div
          role="tablist"
          aria-label="Подвкладки настроек"
          className="mt-4 flex gap-2 overflow-x-auto pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        >
          {SETTINGS_SECTIONS.map(section => {
            const SectionIcon = section.Icon;
            const active = activeSettingsSection === section.id;
            const statusText = dirtyTemplateCount > 0
              ? `${dirtyTemplateCount} черновик`
              : `${customTemplateCount}/${templates.length}`;

            return (
              <button
                key={section.id}
                id={`settings-tab-${section.id}`}
                type="button"
                role="tab"
                aria-selected={active}
                aria-controls={`settings-panel-${section.id}`}
                onClick={() => setActiveSettingsSection(section.id)}
                className={`smooth-pressable flex min-h-[52px] min-w-[210px] shrink-0 items-center gap-3 rounded-2xl border px-3 py-2 text-left transition-all sm:min-w-[240px] ${
                  active
                    ? 'border-cyan-300/35 bg-cyan-300/[0.13] text-white shadow-neon-cyan'
                    : 'border-white/10 bg-white/[0.035] text-slate-300 hover:border-white/20 hover:bg-white/[0.06]'
                }`}
              >
                <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border ${
                  active
                    ? 'border-cyan-300/35 bg-cyan-300/15 text-cyan-100'
                    : 'border-white/10 bg-black/20 text-slate-400'
                }`}>
                  <SectionIcon className="h-4 w-4" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[11px] font-black uppercase tracking-wider">
                    {section.title}
                  </span>
                  <span className="mt-0.5 block truncate text-[10px] font-bold text-slate-400">
                    {section.subtitle}
                  </span>
                </span>
                <span className="shrink-0 rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-[8px] font-black uppercase tracking-wider text-cyan-100">
                  {statusText}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {activeSettingsSection === 'message_texts' && (
      <div
        id="settings-panel-message_texts"
        role="tabpanel"
        aria-labelledby="settings-tab-message_texts"
        className="grid min-w-0 grid-cols-1 gap-4 xl:grid-cols-[360px_minmax(0,1fr)]"
      >
        <aside className="space-y-3 rounded-3xl border border-white/10 bg-[#050b16]/88 p-3 shadow-glass backdrop-blur-xl sm:p-4 xl:sticky xl:top-4 xl:self-start">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs font-black uppercase tracking-wider text-white">Шаблоны</p>
              <p className="mt-0.5 truncate text-[10px] font-bold text-slate-500">
                Выберите текст и редактируйте ниже
              </p>
            </div>
            <span className="shrink-0 rounded-xl border border-white/10 bg-white/[0.04] px-2.5 py-1.5 text-[9px] font-black uppercase tracking-wider text-slate-300">
              {templates.length}
            </span>
          </div>

          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
            <input
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
              placeholder="Найти шаблон"
              className="w-full rounded-2xl border border-white/10 bg-slate-900/70 py-2.5 pl-9 pr-3 text-[16px] font-bold text-white placeholder-slate-600 outline-none transition-all focus:border-cyan-300/45 sm:text-xs"
            />
          </div>

          <div className="max-h-[34dvh] space-y-2.5 overflow-y-auto pr-1 overscroll-contain lg:max-h-none lg:overflow-visible lg:pr-0 xl:max-h-[calc(100dvh-220px)] xl:overflow-y-auto xl:pr-1">
            {groupedTemplates.map(group => {
              const tone = GROUP_TONE[group.id];
              const hasSearch = Boolean(searchTerm.trim());
              const open = hasSearch || expandedGroups[group.id];
              const dirtyCount = group.templates.filter(template => (
                (drafts[template.key] ?? template.body) !== template.body
              )).length;
              const customCount = group.templates.filter(template => template.is_custom).length;
              const GroupIcon = group.Icon;

              return (
                <div key={group.id} className="overflow-hidden rounded-2xl border border-white/[0.08] bg-black/20">
                  <button
                    type="button"
                    onClick={() => toggleGroup(group.id)}
                    aria-expanded={open}
                    className={`smooth-pressable flex w-full items-center gap-3 border px-3 py-3 text-left transition-all ${tone.header}`}
                  >
                    <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border text-[10px] font-black ${tone.marker}`}>
                      <GroupIcon className="h-4 w-4" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex min-w-0 items-center gap-2">
                        <span className="truncate text-xs font-black uppercase tracking-wider text-white">
                          {group.title}
                        </span>
                        <span className={`shrink-0 rounded-lg border px-1.5 py-0.5 text-[8px] font-black uppercase tracking-wider ${tone.marker}`}>
                          {group.badge}
                        </span>
                      </span>
                      <span className="mt-0.5 block truncate text-[10px] font-bold text-slate-400">
                        {group.subtitle}
                      </span>
                    </span>
                    <span className="flex shrink-0 items-center gap-2">
                      <span className="rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-[8px] font-black uppercase tracking-wider text-slate-300">
                        {group.templates.length}/{group.total}
                      </span>
                      {(dirtyCount > 0 || customCount > 0) && (
                        <span className="rounded-lg border border-amber-300/20 bg-amber-300/10 px-2 py-1 text-[8px] font-black uppercase tracking-wider text-amber-100">
                          {dirtyCount || customCount}
                        </span>
                      )}
                      <ChevronDown className={`h-4 w-4 text-slate-300 transition-transform ${open ? 'rotate-180' : ''}`} />
                    </span>
                  </button>

                  <SmoothCollapse open={open}>
                    <div className="space-y-2 p-2">
                      {group.templates.length === 0 ? (
                        <div className="rounded-xl border border-white/[0.06] bg-white/[0.025] px-3 py-3 text-[10px] font-bold text-slate-500">
                          Ничего не найдено
                        </div>
                      ) : group.templates.map(template => {
                        const active = activeTemplate?.key === template.key;
                        const dirty = (drafts[template.key] ?? template.body) !== template.body;
                        return (
                          <button
                            key={`${group.id}-${template.key}`}
                            type="button"
                            onClick={() => handleSelectTemplate(template.key)}
                            className={`w-full rounded-xl border p-3 text-left transition-all active:scale-[0.99] ${
                              active
                                ? tone.activeCard
                                : 'border-white/[0.08] bg-[#07101d]/78 hover:border-white/[0.16] hover:bg-[#0b1728]'
                            }`}
                          >
                            <div className="flex items-start justify-between gap-2">
                              <div className="min-w-0">
                                <p className="truncate text-xs font-black text-white">{template.title}</p>
                                <p className="mt-1 line-clamp-2 text-[10px] font-semibold leading-relaxed text-slate-400">
                                  {template.description}
                                </p>
                              </div>
                              <span className={`shrink-0 rounded-lg px-2 py-1 text-[8px] font-black uppercase tracking-wider ${
                                dirty
                                  ? 'bg-amber-400/15 text-amber-200'
                                  : template.is_custom
                                    ? 'bg-emerald-400/15 text-emerald-200'
                                    : 'bg-slate-800 text-slate-400'
                              }`}>
                                {dirty ? 'черновик' : template.is_custom ? 'свой' : 'база'}
                              </span>
                            </div>
                          </button>
                        );
                      })}
                    </div>
                  </SmoothCollapse>
                </div>
              );
            })}
          </div>
        </aside>

        {activeTemplate && (
          <section ref={editorPanelRef} className="scroll-mt-4 grid min-w-0 grid-cols-1 gap-4 2xl:grid-cols-[minmax(0,1fr)_360px]">
            <div className="space-y-4 rounded-3xl border border-white/10 bg-slate-950/38 p-3 shadow-glass backdrop-blur-xl sm:p-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <h3 className="text-base font-black leading-snug text-white sm:truncate">{activeTemplate.title}</h3>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-[10px] font-bold uppercase tracking-wider text-slate-500">
                    <span className="flex items-center gap-1">
                      <Clock3 className="h-3.5 w-3.5" />
                      {formatTemplateDate(activeTemplate.updated_at)}
                    </span>
                    {isDirty && <span className="text-amber-200">есть несохраненные правки</span>}
                  </div>
                </div>

                {renderTemplateActions(activeTemplate, 'grid grid-cols-2 gap-2 sm:flex')}
              </div>

              <div className="space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-2 text-[10px] font-black uppercase tracking-wider text-slate-400">
                  <span>Текст сообщения</span>
                  <span className="rounded-lg border border-cyan-300/15 bg-cyan-300/[0.07] px-2 py-1 text-cyan-100">
                    {activeTemplate.variables.length} поля защищены
                  </span>
                </div>
                <div className="flex flex-wrap items-start gap-2 rounded-2xl border border-cyan-300/20 bg-[#07111f] p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.05),0_18px_48px_rgba(0,0,0,0.25)]">
                  {editorTokens.map((token, index) => {
                    if (token.type === 'markup') return null;
                    if (token.type === 'variable') {
                      return (
                        <div
                          key={`${token.type}-${index}-${token.key}`}
                          className="inline-flex max-w-full items-center gap-2 rounded-xl border border-cyan-300/25 bg-cyan-300/[0.11] px-3 py-2 text-[11px] font-black text-cyan-50 shadow-[0_0_18px_rgba(34,211,238,0.08)]"
                        >
                          <Braces className="h-3.5 w-3.5 shrink-0 text-cyan-200" />
                          <span className="truncate">{token.label}</span>
                          {token.example && (
                            <span className="hidden max-w-[140px] truncate rounded-lg bg-black/20 px-2 py-1 text-[9px] font-bold text-cyan-100/70 sm:inline">
                              {token.example}
                            </span>
                          )}
                        </div>
                      );
                    }
                    if (!token.value.trim()) return null;
                    const compactText = !token.value.includes('\n') && token.value.trim().length <= 18;
                    if (compactText) {
                      return (
                        <input
                          key={`${token.type}-${index}`}
                          value={token.value}
                          onChange={(event) => handleTextTokenChange(index, event.target.value)}
                          spellCheck={false}
                          aria-label={`Редактируемый текст ${index + 1}`}
                          style={{ '--editor-token-width': `${Math.max(4, Math.min(22, token.value.length + 2))}ch` } as React.CSSProperties}
                          className="min-h-[42px] w-full max-w-full rounded-xl border border-slate-600/70 bg-[#0b1728] px-3 text-[16px] font-semibold text-[#e8f3ff] shadow-inner outline-none transition-all [color-scheme:dark] selection:bg-cyan-300/25 focus:border-cyan-300/65 focus:bg-[#0d1b30] focus:ring-2 focus:ring-cyan-300/15 sm:w-[var(--editor-token-width)] sm:text-[13px]"
                        />
                      );
                    }
                    return (
                      <textarea
                        key={`${token.type}-${index}`}
                        value={token.value}
                        onChange={(event) => handleTextTokenChange(index, event.target.value)}
                        rows={rowsForEditableText(token.value)}
                        spellCheck={false}
                        aria-label={`Редактируемый текст ${index + 1}`}
                        className="w-full basis-full resize-y rounded-xl border border-slate-600/70 bg-[#0b1728] px-3.5 py-3 text-[16px] font-semibold leading-relaxed text-[#e8f3ff] shadow-inner outline-none transition-all [color-scheme:dark] selection:bg-cyan-300/25 placeholder:text-slate-500 focus:border-cyan-300/65 focus:bg-[#0d1b30] focus:ring-2 focus:ring-cyan-300/15 sm:text-[13px]"
                      />
                    );
                  })}
                </div>
              </div>

              <div className="space-y-2">
                <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-wider text-slate-400">
                  <Braces className="h-4 w-4 text-cyan-200" />
                  <span>Поля</span>
                </div>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {activeTemplate.variables.map(variable => (
                    <div
                      key={variable.key}
                      className="rounded-xl border border-white/[0.08] bg-white/[0.035] px-3 py-2"
                    >
                      <p className="truncate text-[10px] font-black text-cyan-100">{variable.label}</p>
                      {variable.example && (
                        <p className="mt-1 truncate text-[9px] font-semibold text-slate-500">{variable.example}</p>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              {renderTemplateActions(activeTemplate, 'grid grid-cols-2 gap-2 border-t border-white/10 pt-3 sm:hidden')}
            </div>

            <div className="space-y-4">
              <div className="rounded-3xl border border-white/10 bg-slate-950/45 p-3 shadow-glass backdrop-blur-xl sm:p-4">
                <div className="mb-3 flex items-center justify-between gap-2">
                  <p className="text-[10px] font-black uppercase tracking-wider text-slate-400">Предпросмотр</p>
                  <span className="rounded-lg bg-slate-800 px-2 py-1 text-[8px] font-black uppercase tracking-wider text-slate-400">
                    итог
                  </span>
                </div>
                <pre className="max-h-[42dvh] whitespace-pre-wrap break-words rounded-2xl border border-white/10 bg-[#07111f] p-3 text-[12px] font-semibold leading-relaxed text-slate-100 sm:max-h-[520px] sm:p-4">
                  {activePreview || 'Пустой предпросмотр'}
                </pre>
              </div>
            </div>
          </section>
        )}
      </div>
      )}
    </div>
  );
}
