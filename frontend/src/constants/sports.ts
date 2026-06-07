export interface SportOption {
  label: string;
  icon: string;
}

export const SPORT_OPTIONS: SportOption[] = [
  { label: 'Автогонки', icon: '/sports/auto-racing.png' },
  { label: 'Ам. футбол', icon: '/sports/american-football.png' },
  { label: 'Бадминтон', icon: '/sports/badminton.png' },
  { label: 'Баскетбол', icon: '/sports/basketball.png' },
  { label: 'Бейсбол', icon: '/sports/baseball.png' },
  { label: 'Бильярд', icon: '/sports/billiards.png' },
  { label: 'Бокс', icon: '/sports/boxing.png' },
  { label: 'Велоспорт', icon: '/sports/cycling.png' },
  { label: 'Вод. поло', icon: '/sports/water-polo.png' },
  { label: 'Водные виды', icon: '/sports/water-sports.png' },
  { label: 'Волейбол', icon: '/sports/volleyball.png' },
  { label: 'Гандбол', icon: '/sports/handball.png' },
  { label: 'Гимнастика', icon: '/sports/gymnastics.png' },
  { label: 'Гольф', icon: '/sports/golf.png' },
  { label: 'Дартс', icon: '/sports/darts.png' },
  { label: 'Другие', icon: '/sports/other.png' },
  { label: 'Единоборства', icon: '/sports/martial-arts.png' },
  { label: 'Киберспорт', icon: '/sports/esports.png' },
  { label: 'Коньки', icon: '/sports/skating.png' },
  { label: 'Крикет', icon: '/sports/cricket.png' },
  { label: 'Л/Атл', icon: '/sports/athletics.png' },
  { label: 'Лыжи/Биатлон', icon: '/sports/skiing-biathlon.png' },
  { label: 'Н/Т', icon: '/sports/table-tennis.png' },
  { label: 'Пляж. футб', icon: '/sports/beach-football.png' },
  { label: 'Регби', icon: '/sports/rugby.png' },
  { label: 'Сани/Бобслей', icon: '/sports/luge-bobsleigh.png' },
  { label: 'Теннис', icon: '/sports/tennis.png' },
  { label: 'Футбол', icon: '/sports/football.png' },
  { label: 'Футзал', icon: '/sports/futsal.png' },
  { label: 'Хоккей', icon: '/sports/hockey.png' },
];

export const SPORT_FILTER_OPTIONS = ['Все', ...SPORT_OPTIONS.map((sport) => sport.label)] as const;
export const ALL_SPORT_LABELS = SPORT_OPTIONS.map((sport) => sport.label);

export function getSportIconSrc(label: string | null | undefined) {
  return SPORT_OPTIONS.find((sport) => sport.label === label)?.icon ?? '/sports/other.png';
}
