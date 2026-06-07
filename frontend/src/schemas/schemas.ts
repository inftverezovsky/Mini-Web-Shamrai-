export interface BookmakerResponse {
  id: number;
  name: string;
  code: string;
  is_active: boolean;
}

export interface UserBadgeResponse {
  id: number;
  user_id: number;
  title: string;
  icon_type: string;
  unlocked_at: string;
}

export interface UserResponse {
  telegram_id: number;
  username: string | null;
  first_name: string | null;
  last_name: string | null;
  role: 'owner' | 'admin' | 'moderator' | 'user';
  stats_display_mode: 'percent' | 'flat';
  bankroll: number;
  is_onboarded: boolean;
  experience_level: 'novice' | 'amateur' | 'pro' | null;
  bankroll_size: 'micro' | 'mid' | 'high' | null;
  favorite_sports: string[];
  risk_tolerance: 'cautious' | 'balanced' | 'aggressive' | null;
  primary_bookmaker: string | null;
  currency_preference: string;
  purchased_bets_balance: number;
  free_bets_available: number;
  matches_remaining: number;
  guarantee_active: boolean;
  guarantee_opened_from_bet_id: string | null;
  guarantee_closed_at: string | null;
  onboarding_goal: string | null;
  ab_group: string | null;
  tg_chat_joined: boolean;
  has_used_shield: boolean;
  alert_min_coef: number;
  is_night_mode: boolean;
  preferred_sports: string[];
  other_bookmaker_name: string | null;
  client_group: string | null;
  client_tag: string | null;
  created_at: string;
  updated_at: string;
  bookmakers: BookmakerResponse[];
  badges: UserBadgeResponse[];
}

export interface SubscriptionPlanResponse {
  id: number;
  name: string;
  duration_days: number;
  match_count: number;
  price: string | number;
  price_stars: number;
  currency: string;
  is_active: boolean;
}

export interface SubscriptionResponse {
  id: string;
  user_id: number;
  plan_id: number;
  status: 'active' | 'expired' | 'pending';
  payment_provider: string | null;
  payment_id: string | null;
  start_date: string | null;
  end_date: string | null;
  created_at: string;
  plan: SubscriptionPlanResponse | null;
}

export interface BookmakerLink {
  bookmaker_id: number;
  url: string;
}

export interface BetResponse {
  id: string;
  event_name: string;
  coefficient: string | number;
  bookmaker_id: number | null;
  description: string | null;
  status: 'pending' | 'win' | 'loss' | 'refund';
  author_id: number | null;
  created_at: string;
  resolved_at: string | null;
  bookmaker: BookmakerResponse | null;
  bookmakers: BookmakerResponse[];
  price_stars: number | null;
  is_unlocked: boolean;
  is_taken: boolean;
  guarantee_count: number;
  supercompensation_count: number;
  refund_count: number;
  category: string;
  live_ends_at: string | null;
  brain_score: number | null;
  api_match_id: string | null;
  sport_type: string | null;
  outcome: string | null;
  coupon_image_url: string | null;
  match_link: string | null;
  bookmaker_links: BookmakerLink[];
  delivery_mode: 'feed' | 'sales_private';
}

export interface ForecastRequestUserResponse {
  telegram_id: number;
  username: string | null;
  first_name: string | null;
  last_name: string | null;
  matches_remaining: number;
  guarantee_active: boolean;
  bookmakers: BookmakerResponse[];
}

export interface ForecastRequestResponse {
  id: string;
  bet_id: string;
  user_id: number;
  status: 'announced' | 'interested' | 'processing' | 'declined' | 'sent' | 'manual_sent' | 'cancelled';
  delivery_method: 'bot' | 'manual' | null;
  handled_by: number | null;
  responded_at: string | null;
  delivered_at: string | null;
  balance_before: number | null;
  balance_after: number | null;
  no_balance_warning: boolean;
  created_at: string;
  updated_at: string;
  bet: BetResponse;
  user: ForecastRequestUserResponse;
}

export interface UserStats {
  total_bets_taken: number;
  won_bets: number;
  lost_bets: number;
  refund_bets: number;
  net_profit: string | number;
  winrate: number;
  roi: number;
  average_coefficient: number;
}

export interface AdminAnalytics {
  total_subscribers: number;
  active_subscriptions: number;
  total_bets_issued: number;
  winrate: number;
  roi: number;
  net_profit: string | number;
  average_coefficient: number;
}

export interface SwipeCandidateResponse {
  bet_id: string;
  match_name: string;
  bookmaker_name: string | null;
  coefficient: string | number;
  options: string[];
}

export interface SwipeResponse {
  match: boolean;
  discount: number;
  promo_code: string | null;
  message: string;
}

export interface QuizQuestionResponse {
  id: string;
  question: string;
  options: string[];
}

export interface QuizActiveResponse {
  id: number;
  bet_id: string;
  discount_reward: number;
  questions: QuizQuestionResponse[];
}

export interface QuizSubmitResponse {
  passed: boolean;
  score: number;
  total: number;
  discount: number;
  promo_code: string | null;
  message: string;
}

export interface PvPBattleResponse {
  id: number;
  match_name: string;
  option_a: string;
  option_b: string;
  votes_a: number;
  votes_b: number;
  percent_a: number;
  percent_b: number;
}

export interface PvPVoteResponse extends PvPBattleResponse {
  selected_option: string;
  message: string;
}

export interface CrowdBetResponse {
  id: number;
  bet_id: string;
  target_amount: number;
  current_amount: number;
  status: 'funding' | 'opened';
  progress_percent: number;
  is_participant: boolean;
}

export interface BetHintResponse {
  bet_id: string;
  paid_xtr: number;
  hint: string;
  reveal_level: 'analysis_only';
}
