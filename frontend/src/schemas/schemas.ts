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
  phone: string | null;
  photo_url: string | null;
  is_web_only: boolean;
  role: 'owner' | 'admin' | 'moderator' | 'user';
  stats_display_mode: 'percent' | 'flat';
  bankroll: number;
  is_onboarded: boolean;
  experience_level: 'novice' | 'amateur' | 'pro' | null;
  bankroll_size: 'micro' | 'mid' | 'high' | null;
  favorite_sports: string[];
  risk_tolerance: 'cautious' | 'balanced' | 'aggressive' | null;
  primary_bookmaker: string | null;
  vk_user_id: string | null;
  vk_group_member: boolean;
  vk_messages_allowed: boolean;
  vk_notifications_allowed: boolean;
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
  odds_drop_notifications_enabled: boolean;
  is_night_mode: boolean;
  night_mode_start: string;
  night_mode_end: string;
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
  fair_coefficient: string | number | null;
  bookmaker_id: number | null;
  description: string | null;
  teaser_text: string | null;
  status: 'pending' | 'win' | 'loss' | 'refund' | 'deleted';
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
  delivery_mode: 'feed' | 'sales_private' | 'paid_set';
  auto_send_on_interest: boolean;
  odds_dropped_to: string | number | null;
  odds_drop_notified_at: string | null;
}

export interface ForecastRequestUserResponse {
  telegram_id: number;
  username: string | null;
  first_name: string | null;
  last_name: string | null;
  photo_url: string | null;
  is_web_only: boolean;
  matches_remaining: number;
  guarantee_active: boolean;
  bookmakers: BookmakerResponse[];
}

export interface ForecastRequestResponse {
  id: string;
  bet_id: string;
  user_id: number;
  status: 'announced' | 'interested' | 'processing' | 'declined' | 'sent' | 'manual_sent' | 'cancelled' | 'removed';
  delivery_method: 'bot' | 'vk' | 'vk_bot' | 'web' | 'manual' | null;
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

export interface PaginatedResponse<T> {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
  total?: number;
  filtered_total?: number;
}

export interface UserPreferencesResponse {
  alert_min_coef: number;
  odds_drop_notifications_enabled: boolean;
  is_night_mode: boolean;
  night_mode_start: string;
  night_mode_end: string;
  preferred_sports?: string[];
  stats_display_mode: 'percent' | 'flat';
}

export interface ReferralInfoResponse {
  referral_code: string;
  referral_link: string;
  invited_count: number;
  purchased_invited_count: number;
  discount_step_percent: number;
  referral_discount_percent: number;
  earned_bonus_days?: number;
  pending_rewards?: number;
}

export interface PaymentHistoryResponse {
  transactions: Array<{
    id: string | number;
    plan_name: string;
    amount: number | string;
    amount_currency?: string;
    amount_stars?: number | null;
    payment_provider?: string | null;
    status: string;
    created_at: string;
    start_date?: string | null;
    end_date?: string | null;
  }>;
  total: number;
}

export interface VkDeliveryStatusResponse {
  vk_user_id: string | null;
  group_id: number | null;
  configured: boolean;
  group_member: boolean;
  messages_allowed: boolean;
  notifications_allowed: boolean;
}

export interface ProfileDashboardResponse {
  user: UserResponse;
  bookmakers: BookmakerResponse[];
  selected_bookmaker_ids: number[];
  preferences: UserPreferencesResponse;
  subscription: SubscriptionResponse | null;
  payments: PaymentHistoryResponse;
  referral: ReferralInfoResponse;
  vk_delivery_status: VkDeliveryStatusResponse;
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

export interface MessageTemplateVariableResponse {
  key: string;
  label: string;
  example: string;
}

export interface MessageTemplateResponse {
  key: string;
  title: string;
  description: string;
  body: string;
  default_body: string;
  variables: MessageTemplateVariableResponse[];
  is_custom: boolean;
  updated_by: number | null;
  created_at: string | null;
  updated_at: string | null;
}

export type SupportChatDirection = 'staff' | 'client';

export interface SupportChatUserResponse {
  telegram_id: number;
  username: string | null;
  first_name: string | null;
  last_name: string | null;
  photo_url: string | null;
  is_web_only: boolean;
  role: string;
  display_name: string;
}

export interface SupportChatMessageResponse {
  id: number;
  user_id: number;
  text: string;
  type: 'support_staff_message' | 'support_client_message';
  data: Record<string, any>;
  created_at: string;
  direction: SupportChatDirection;
  author_label: string;
  sender_user_id: number | null;
  sender_role: string | null;
}

export interface SupportChatThreadResponse {
  user: SupportChatUserResponse;
  last_message: SupportChatMessageResponse | null;
  last_message_text: string | null;
  last_message_created_at: string | null;
  needs_reply: boolean;
}

export interface SupportChatThreadListResponse {
  items: SupportChatThreadResponse[];
}

export type ChatSupportDirection = 'staff' | 'client';
export type ChatConversationKind = 'signals' | 'support';
export type ChatConversationStatus = 'open' | 'closed';

export interface ChatUserResponse {
  telegram_id: number;
  username: string | null;
  first_name: string | null;
  last_name: string | null;
  photo_url: string | null;
  is_web_only: boolean;
  role: string;
  display_name: string;
}

export interface ChatMessageResponse {
  id: number;
  conversation_id: string;
  sender_user_id: number | null;
  sender_role: string;
  direction: ChatSupportDirection;
  author_label: string;
  type: 'text' | 'image' | 'voice';
  text: string | null;
  payload: Record<string, any>;
  client_message_id: string;
  reply_to_id: number | null;
  created_at: string;
  edited_at: string | null;
  deleted_at: string | null;
}

export interface ChatSignalMessageResponse {
  id: number;
  user_id: number;
  text: string;
  type: string;
  data: Record<string, any>;
  created_at: string;
  direction?: ChatSupportDirection | null;
  author_label?: string | null;
  sender_user_id?: number | null;
  sender_role?: string | null;
}

export interface ChatConversationResponse {
  id: string | null;
  kind: ChatConversationKind;
  key: string;
  status: ChatConversationStatus;
  owner_user: ChatUserResponse;
  assigned_staff_id: number | null;
  last_message: ChatMessageResponse | ChatSignalMessageResponse | null;
  last_message_text: string | null;
  last_message_at: string | null;
  unread_count: number;
}

export interface ChatConversationListResponse {
  items: ChatConversationResponse[];
  next_before: string | null;
  has_more: boolean;
}

export interface ChatMessagePageResponse {
  items: ChatMessageResponse[];
  next_before_id: number | null;
  has_more: boolean;
}

export interface ChatSignalMessagePageResponse {
  items: ChatSignalMessageResponse[];
  next_before_id: number | null;
  has_more: boolean;
}

export interface ChatReadResponse {
  status: string;
  last_read_message_id: number | null;
  last_read_signal_id: number | null;
}

export type PeriodFilter = 'week' | 'month' | 'quarter' | 'all';

export interface PerformanceSummary {
  bets: number;
  wins: number;
  losses: number;
  winrate: number;
  roi: number;
  profit_units: number;
  average_coefficient: number;
  max_win_streak: number;
  max_loss_streak: number;
  current_streak: number;
  current_streak_type: 'win' | 'loss' | null;
}

export interface PerformanceBetItem {
  id: string;
  event_name: string;
  status: 'win' | 'loss';
  coefficient: number;
  bookmaker_id?: number | null;
  description?: string | null;
  profit_units: number;
  resolved_at: string;
  created_at: string | null;
  taken_at: string | null;
  delivery_mode: 'feed' | 'sales_private' | 'paid_set';
  source_type: 'feed' | 'private' | 'paid_set';
  sport_type: string | null;
  outcome: string | null;
  match_link?: string | null;
  bookmakers: Array<Pick<BookmakerResponse, 'id' | 'name' | 'code'>>;
  bookmaker_names: string[];
  bookmaker_links?: BookmakerLink[];
  access_type?: string | null;
  match_charged?: boolean | null;
}

export interface PerformanceDayGroup {
  key: string;
  label: string;
  summary: PerformanceSummary;
  bets: PerformanceBetItem[];
}

export interface PerformanceMonthGroup {
  key: string;
  label: string;
  summary: PerformanceSummary;
  days: PerformanceDayGroup[];
}

export interface PerformanceBreakdownItem {
  key: string;
  label: string;
  summary: PerformanceSummary;
}

export interface SourceSplit {
  all: PerformanceSummary;
  feed: PerformanceSummary;
  private: PerformanceSummary;
  paid_set: PerformanceSummary;
}

export interface PerformanceTimelineResponse {
  period: PeriodFilter;
  period_label: string;
  summary: PerformanceSummary;
  source_split: SourceSplit;
  timeline: PerformanceMonthGroup[];
  bookmaker_breakdown: PerformanceBreakdownItem[];
  sport_breakdown: PerformanceBreakdownItem[];
  default_expanded_month_key: string;
  default_expanded_day_key: string;
  excluded_summary?: PerformanceSummary;
  excluded_bets?: PerformanceBetItem[];
}

export interface ClientSituation {
  code: string;
  label: string;
  tone: 'success' | 'warning' | 'danger' | 'neutral';
  description: string;
}

export interface AdminAuthorTimelineResponse extends PerformanceTimelineResponse {
  author: {
    telegram_id: number;
    name: string;
    username: string | null;
  };
}

export interface AdminClientStatsItem {
  telegram_id: number;
  username: string | null;
  first_name: string | null;
  last_name: string | null;
  photo_url: string | null;
  name: string;
  matches_remaining: number;
  guarantee_active: boolean;
  client_group: string | null;
  client_tag: string | null;
  summary: PerformanceSummary;
  source_split: SourceSplit;
  recent_results: Array<'win' | 'loss'>;
  situation: ClientSituation;
}

export interface AdminClientsStatsResponse {
  period: PeriodFilter;
  period_label: string;
  clients_count: number;
  active_clients_count: number;
  active_clients_with_stats_count: number;
  summary: PerformanceSummary;
  clients: AdminClientStatsItem[];
}

export interface AdminClientTimelineResponse extends PerformanceTimelineResponse {
  user: Omit<AdminClientStatsItem, 'summary' | 'source_split' | 'recent_results' | 'situation'>;
  situation: ClientSituation;
  recent_results: Array<'win' | 'loss'>;
}

export type StatsDriveExportScope = 'shamrai' | 'clients' | 'all' | 'crm';
export type StatsDriveExportFormat = 'xlsx' | 'google_sheet';

export interface StatsDriveExportLink {
  title: string;
  url: string;
  id: string;
  format: 'folder' | StatsDriveExportFormat;
}

export interface StatsDriveExportJob {
  id: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  scope: StatsDriveExportScope;
  period: PeriodFilter;
  formats: StatsDriveExportFormat[];
  filters?: Record<string, string>;
  links: StatsDriveExportLink[];
  error: string | null;
  created_at: string;
  updated_at: string;
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

export interface CrowdBetFundResponse {
  crowd_bet: CrowdBetResponse;
  attempt_id: string;
  invoice_url: string;
  amount_xtr: number;
  status: 'invoice_created';
}

export interface BetHintResponse {
  bet_id: string;
  paid_xtr: number;
  hint: string;
  reveal_level: 'analysis_only';
}

export interface BetHintInvoiceResponse {
  bet_id: string;
  attempt_id: string;
  invoice_url: string;
  price_xtr: number;
  status: 'invoice_created';
}
