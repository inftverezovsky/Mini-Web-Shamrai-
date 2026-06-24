import { ExternalLink } from 'lucide-react';

interface TelegramAuthAssistProps {
  botUrl: string | null;
  className?: string;
}

export default function TelegramAuthAssist({ botUrl, className = '' }: TelegramAuthAssistProps) {
  if (!botUrl) return null;

  return (
    <div className={`rounded-xl border border-cyan-300/20 bg-cyan-400/10 px-3 py-2 text-[11px] font-bold leading-relaxed text-cyan-50 ${className}`}>
      <p>Telegram открыт. Нажмите Start в боте, затем вернитесь сюда.</p>
      <a
        href={botUrl}
        target="_blank"
        rel="noreferrer"
        className="mt-2 inline-flex min-h-[32px] items-center justify-center gap-1.5 rounded-lg border border-cyan-200/25 bg-white/10 px-2.5 py-1.5 text-[10px] font-black uppercase tracking-wider text-white transition hover:bg-white/15 active:scale-[0.98]"
      >
        <ExternalLink className="h-3.5 w-3.5" />
        <span>Открыть Telegram еще раз</span>
      </a>
    </div>
  );
}
