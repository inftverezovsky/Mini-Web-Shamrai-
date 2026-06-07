import React, { useState, useEffect } from 'react';
import { apiFetch } from '../../utils/api';
import { BetResponse, UserStats } from '../../schemas/schemas';
import { Trophy, Calendar, Loader2, TrendingUp, AlertCircle, BookOpen } from 'lucide-react';
import { BookmakerLogoFrame, SportIconFrame } from '../../components/LogoFrame';

export default function MyBets() {
  const [bets, setBets] = useState<BetResponse[]>([]);
  const [stats, setStats] = useState<UserStats | null>(null);
  
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Capper Diary Notes State
  const [expandedNotes, setExpandedNotes] = useState<Record<string, boolean>>({});
  const [noteTexts, setNoteTexts] = useState<Record<string, string>>({});
  const [noteEmotions, setNoteEmotions] = useState<Record<string, number>>({});
  const [loadingNotes, setLoadingNotes] = useState<Record<string, boolean>>({});
  const [savingNotes, setSavingNotes] = useState<Record<string, boolean>>({});

  const toggleNoteDiary = async (betId: string) => {
    const isExpanded = !!expandedNotes[betId];
    setExpandedNotes(prev => ({ ...prev, [betId]: !isExpanded }));

    // Fetch note if opening and not loaded yet
    if (!isExpanded && noteTexts[betId] === undefined) {
      try {
        setLoadingNotes(prev => ({ ...prev, [betId]: true }));
        const note = await apiFetch(`/bets/${betId}/notes`);
        if (note) {
          setNoteTexts(prev => ({ ...prev, [betId]: note.text }));
          setNoteEmotions(prev => ({ ...prev, [betId]: note.emotion_score }));
        } else {
          setNoteTexts(prev => ({ ...prev, [betId]: '' }));
          setNoteEmotions(prev => ({ ...prev, [betId]: 5 }));
        }
      } catch (err) {
        console.error('Failed to load diary note:', err);
      } finally {
        setLoadingNotes(prev => ({ ...prev, [betId]: false }));
      }
    }
  };

  const handleSaveNote = async (betId: string) => {
    try {
      setSavingNotes(prev => ({ ...prev, [betId]: true }));
      const payload = {
        text: noteTexts[betId] || '',
        emotion_score: noteEmotions[betId] || 5
      };
      await apiFetch(`/bets/${betId}/notes`, {
        method: 'POST',
        body: JSON.stringify(payload)
      });
      alert('Заметка сохранена!');
    } catch (err: any) {
      alert(err.message || 'Ошибка сохранения');
    } finally {
      setSavingNotes(prev => ({ ...prev, [betId]: false }));
    }
  };

  const loadMyBets = async () => {
    try {
      setLoading(true);
      setError(null);
      
      // GET /api/users/me/bets
      const data = await apiFetch('/users/me/bets');
      setBets(data);

      // GET /api/bets/stats for user indicators
      const statsData = await apiFetch('/bets/stats');
      setStats(statsData);
    } catch (err: any) {
      console.error(err);
      setError(err.message || 'Ошибка загрузки ставок');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadMyBets();
  }, []);

  const formatDate = (dateStr: string) => {
    const d = new Date(dateStr);
    return d.toLocaleString('ru-RU', {
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[40vh] space-y-3">
        <Loader2 className="w-7 h-7 text-emerald-500 animate-spin" />
        <span className="text-slate-400 text-xs">Загрузка ваших ставок...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center p-6 text-rose-400 text-xs flex flex-col items-center space-y-2">
        <AlertCircle className="w-8 h-8" />
        <span>Ошибка: {error}</span>
        <button onClick={loadMyBets} className="underline text-indigo-400">Повторить</button>
      </div>
    );
  }

  const totalBetsTaken = stats?.total_bets_taken ?? bets.length;
  const winrateNum = stats?.winrate ?? 0;
  const averageCoefficient = stats?.average_coefficient ?? (
    bets.length > 0
      ? bets.reduce((sum, bet) => sum + Number(bet.coefficient || 0), 0) / bets.length
      : 0
  );

  return (
    <div className="space-y-6 animate-slide-up pb-10">
      
      {/* Header section with User stats */}
      <div className="bg-white/[0.04] border border-white/10 backdrop-blur-md p-4 rounded-2xl space-y-3">
        <div className="flex items-center space-x-1.5 text-slate-400 text-[10px] font-extrabold uppercase tracking-wider">
          <TrendingUp className="w-4 h-4 text-emerald-400" />
          <span>Моя эффективность</span>
        </div>

        <div className="grid grid-cols-3 gap-2 text-center text-xs">
          <div className="bg-black/20 min-h-[70px] px-2 py-3 rounded-xl border border-white/5 flex flex-col justify-center">
            <span className="text-[9px] text-slate-500 font-semibold uppercase leading-tight">Ставок</span>
            <span className="text-base font-black mt-1 text-white">
              {totalBetsTaken}
            </span>
          </div>
          <div className="bg-black/20 min-h-[70px] px-2 py-3 rounded-xl border border-white/5 flex flex-col justify-center">
            <span className="text-[9px] text-slate-500 font-semibold uppercase leading-tight">Побед</span>
            <span className="text-base font-black text-emerald-400 mt-1">
              {winrateNum.toFixed(winrateNum % 1 === 0 ? 0 : 1)}%
            </span>
          </div>
          <div className="bg-black/20 min-h-[70px] px-2 py-3 rounded-xl border border-white/5 flex flex-col justify-center">
            <span className="text-[9px] text-slate-500 font-semibold uppercase leading-tight">Средний КФ</span>
            <span className="text-base font-black text-indigo-400 mt-1">
              {averageCoefficient.toFixed(2)}
            </span>
          </div>
        </div>
      </div>

      {/* Bets list catalog */}
      <div className="space-y-4">
        <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider">Моя история ставок</h3>
        
        {bets.length === 0 ? (
          <div className="glass-panel p-8 text-center rounded-2xl border border-white/5">
            <Trophy className="w-10 h-10 text-slate-600 mx-auto mb-2" />
            <p className="text-slate-400 text-xs font-semibold">История пуста</p>
            <p className="text-slate-500 text-[10px] mt-1">Добавьте прогнозы в отслеживание из Ленты.</p>
          </div>
        ) : (
          bets.map(bet => {
            const isPending = bet.status === 'pending';
            const betBookmakers = bet.bookmakers?.length
              ? bet.bookmakers
              : bet.bookmaker
                ? [bet.bookmaker]
                : [];
            
            return (
              <div 
                key={bet.id}
                className="p-5 rounded-2xl border border-white/10 backdrop-blur-md bg-white/[0.04] space-y-3 relative overflow-hidden transition-all duration-300 hover:scale-[1.02] hover:-translate-y-0.5 motion-card shimmer-border"
              >
                
                {/* Date & Badge Tag */}
                <div className="flex justify-between items-center text-[10px] text-slate-400">
                  <span className="flex items-center">
                    <Calendar className="w-3 h-3 mr-1 text-slate-500" />
                    {formatDate(bet.created_at)}
                  </span>
                  
                  <div className="flex items-center space-x-1.5">
                    {bet.sport_type && (
                      <span className="bg-slate-900/60 border border-white/10 pl-1 pr-2.5 py-0.5 rounded-full font-bold text-[8.5px] uppercase tracking-wider flex items-center gap-1.5 text-slate-200 hover:border-pink-500/30 transition-all duration-300 shadow-sm">
                        <SportIconFrame label={bet.sport_type} size="compact" className="rounded-full overflow-hidden" />
                        {bet.sport_type}
                      </span>
                    )}
                    {/* Settle Badge indicator */}
                    {bet.status === 'pending' && (
                      <span className="bg-amber-500/10 text-amber-400 border border-amber-500/20 px-2.5 py-0.5 rounded-full font-bold">Ожидает</span>
                    )}
                    {bet.status === 'win' && (
                      <span className="bg-emerald-500/15 text-emerald-400 border border-emerald-500/25 px-2.5 py-0.5 rounded-full font-bold shadow-neon-green">Выигрыш 🏆</span>
                    )}
                    {bet.status === 'loss' && (
                      <span className="bg-rose-500/15 text-rose-400 border border-rose-500/25 px-2.5 py-0.5 rounded-full font-bold shadow-neon-red">Проигрыш</span>
                    )}
                    {bet.status === 'refund' && (
                      <span className="bg-slate-800 border border-slate-750 text-slate-400 px-2.5 py-0.5 rounded-full font-bold">Возврат</span>
                    )}
                  </div>
                </div>

                {/* Content Header */}
                <div>
                  <h4 className="text-sm font-extrabold text-white leading-snug">{bet.event_name}</h4>
                  {betBookmakers.length > 0 && (
                    <div className="flex flex-wrap items-center gap-2 mt-2">
                      {betBookmakers.map((bookmaker) => (
                        <span
                          key={bookmaker.id}
                          className="bg-slate-900/60 border border-white/10 pl-1 pr-2.5 py-0.5 rounded-full text-[9px] font-bold text-slate-200 inline-flex items-center gap-1.5 hover:border-cyan-500/30 transition-all duration-300 shadow-sm"
                        >
                          <BookmakerLogoFrame bookmaker={bookmaker} size="badge" className="rounded-full overflow-hidden" />
                          {bookmaker.name}
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                {/* Odds */}
                <div className="flex justify-between items-center bg-black/25 p-3 rounded-xl border border-white/5 text-xs">
                  <div>
                    <span className="text-[9px] uppercase font-bold text-slate-500 block">Коэффициент</span>
                    <span className="text-base font-black text-emerald-400">{parseFloat(bet.coefficient as any).toFixed(2)}</span>
                  </div>
                </div>

                {/* Notes Diary section (only for resolved bets) */}
                {!isPending && (
                  <div className="pt-2.5 border-t border-white/5 space-y-2.5">
                    <button
                      onClick={() => toggleNoteDiary(bet.id)}
                      className="text-[10px] text-slate-400 font-extrabold uppercase hover:text-white transition-all flex items-center space-x-1"
                    >
                      <BookOpen className="w-3.5 h-3.5 mr-1 text-indigo-400" />
                      <span>{expandedNotes[bet.id] ? 'Скрыть дневник' : '📝 Дневник каппера'}</span>
                    </button>

                    {expandedNotes[bet.id] && (
                      <div className="space-y-3 pt-1 border-t border-white/[0.02] animate-slide-down">
                        {loadingNotes[bet.id] ? (
                          <div className="flex justify-center py-2">
                            <Loader2 className="w-4 h-4 text-indigo-500 animate-spin" />
                          </div>
                        ) : (
                          <>
                            <div>
                              <span className="block text-slate-450 font-bold mb-1 uppercase tracking-wider text-[9px]">Ваше настроение:</span>
                              <div className="flex space-x-2 text-lg">
                                {['😡', '😟', '😐', '🙂', '😎'].map((emoji, scoreIdx) => {
                                  const score = scoreIdx + 1;
                                  const isSelected = (noteEmotions[bet.id] || 5) === score;
                                  return (
                                    <button
                                      key={score}
                                      type="button"
                                      onClick={() => setNoteEmotions(prev => ({ ...prev, [bet.id]: score }))}
                                      className={`transition-all duration-200 p-1 rounded-lg ${
                                        isSelected 
                                          ? 'bg-indigo-500/20 border border-indigo-500 scale-125' 
                                          : 'opacity-40 hover:opacity-85'
                                      }`}
                                    >
                                      {emoji}
                                    </button>
                                  );
                                })}
                              </div>
                            </div>

                            <div>
                              <span className="block text-slate-450 font-bold mb-1 uppercase tracking-wider text-[9px]">Заметки по матчу:</span>
                              <textarea
                                value={noteTexts[bet.id] || ''}
                                onChange={e => setNoteTexts(prev => ({ ...prev, [bet.id]: e.target.value }))}
                                placeholder="Опишите свои мысли, ход игры, ошибки или выводы..."
                                rows={3}
                                className="w-full bg-slate-950/60 border border-slate-800/60 rounded-xl py-2 px-3 text-xs text-white placeholder-slate-650 focus:outline-none focus:border-indigo-500/50 transition-all leading-normal"
                              />
                            </div>

                            <button
                              onClick={() => handleSaveNote(bet.id)}
                              disabled={savingNotes[bet.id]}
                              className="w-full bg-indigo-500 hover:bg-indigo-600 active:scale-[0.98] disabled:opacity-50 text-white font-extrabold py-2 rounded-xl text-[10px] uppercase tracking-wider flex items-center justify-center transition-all shadow-glass"
                            >
                              {savingNotes[bet.id] ? (
                                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                              ) : (
                                'Сохранить в дневник'
                              )}
                            </button>
                          </>
                        )}
                      </div>
                    )}
                  </div>
                )}

              </div>
            );
          })
        )}
      </div>

    </div>
  );
}
