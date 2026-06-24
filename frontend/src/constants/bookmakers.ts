import { BookmakerResponse } from '../schemas/schemas';

export const OTHER_BOOKMAKER_CODE = 'other';

const TRANSPARENT_LOGO_DIR = '/bookmakers/transparent';

const BOOKMAKER_LOGOS: Record<string, string> = {
  fonbet: `${TRANSPARENT_LOGO_DIR}/fonbet.png`,
  betboom: `${TRANSPARENT_LOGO_DIR}/betboom.png`,
  winline: `${TRANSPARENT_LOGO_DIR}/winline.png`,
  pari: `${TRANSPARENT_LOGO_DIR}/pari.png`,
  ligastavok: `${TRANSPARENT_LOGO_DIR}/ligastavok.png`,
  marathon: `${TRANSPARENT_LOGO_DIR}/marathon.png`,
  betcity: `${TRANSPARENT_LOGO_DIR}/betcity.png`,
  melbet: `${TRANSPARENT_LOGO_DIR}/melbet.png`,
  leon: `${TRANSPARENT_LOGO_DIR}/leon.png`,
  olimpbet: `${TRANSPARENT_LOGO_DIR}/olimpbet.png`,
  olimp: `${TRANSPARENT_LOGO_DIR}/olimpbet.png`,
  zenit: `${TRANSPARENT_LOGO_DIR}/zenit.png`,
  bettery: `${TRANSPARENT_LOGO_DIR}/bettery.png`,
  [OTHER_BOOKMAKER_CODE]: '/bookmakers/other.jpg',
};

export function getBookmakerLogoSrc(bookmaker: Pick<BookmakerResponse, 'code'>) {
  return BOOKMAKER_LOGOS[bookmaker.code] ?? BOOKMAKER_LOGOS[OTHER_BOOKMAKER_CODE];
}

export function isOtherBookmaker(bookmaker: Pick<BookmakerResponse, 'code'>) {
  return bookmaker.code === OTHER_BOOKMAKER_CODE;
}
