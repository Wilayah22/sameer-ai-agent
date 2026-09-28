import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  ArrowDownLeft,
  ArrowUpLeft,
  BarChart3,
  Bell,
  Check,
  CircleHelp,
  Clock3,
  Gauge,
  HeartHandshake,
  LayoutDashboard,
  LineChart as LineChartIcon,
  LoaderCircle,
  LockKeyhole,
  Menu,
  MessageCircle,
  MicOff,
  MonitorPlay,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Sparkles,
  UserRound,
  UsersRound,
  WifiOff,
  X,
} from 'lucide-react';
import { motion } from 'framer-motion';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

type RecordValue = Record<string, unknown>;

type QualityDimensions = {
  depth: number | null;
  participation: number | null;
  consistency: number | null;
  balance: number | null;
};

type Participant = {
  name: string;
  status: 'participated' | 'attended' | 'silent';
  asked: boolean;
};

type DashboardModel = {
  familyName: string;
  lastSessionAt: string | null;
  totalSessions: number;
  qualityScore: number | null;
  qualityDimensions: QualityDimensions;
  participants: Participant[];
  weeklyQuality: Array<{ label: string; score: number }>;
  categories: Array<{ label: string; score: number }>;
  privacy: { voiceBytes: number; images: number };
  recentSessions: Array<{ topic: string; summary: string | null; date: string | null }>;
};

const NAVY = '#0d2942';
const NAVY_2 = '#173d5c';
const GOLD = '#c59c45';
const GOLD_LIGHT = '#ead7a5';
const PAPER = '#f6f1e7';
const PAPER_2 = '#eee6d5';
const INK = '#203545';
const MUTED = '#71808a';
const LINE = '#d9d1c1';
const SUCCESS = '#52715d';
const WARNING = '#9d7731';
const DANGER = '#9b5e5e';

const EMPTY_DIMENSIONS: QualityDimensions = {
  depth: null,
  participation: null,
  consistency: null,
  balance: null,
};

const EMPTY_MODEL: DashboardModel = {
  familyName: '',
  lastSessionAt: null,
  totalSessions: 0,
  qualityScore: null,
  qualityDimensions: EMPTY_DIMENSIONS,
  participants: [],
  weeklyQuality: [],
  categories: [],
  privacy: { voiceBytes: 0, images: 0 },
  recentSessions: [],
};

const CATEGORY_NAMES = ['دينية', 'اجتماعية', 'مهارات تواصل', 'حياتية', 'عامة'];
const CATEGORY_COLORS = [GOLD, '#8aa0a7', '#6e8ca0', '#8b9e79', '#b5a47b'];

function asRecord(value: unknown): RecordValue {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as RecordValue)
    : {};
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function firstValue(source: RecordValue, keys: string[]): unknown {
  for (const key of keys) {
    if (source[key] !== undefined && source[key] !== null) return source[key];
  }
  return null;
}

function numberValue(source: RecordValue, keys: string[]): number | null {
  const value = firstValue(source, keys);
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function percentageValue(value: unknown): number | null {
  const parsed = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(parsed)) return null;
  const normalized = parsed >= 0 && parsed <= 1 ? parsed * 100 : parsed <= 3 ? (parsed / 3) * 100 : parsed;
  return Math.max(0, Math.min(100, Math.round(normalized)));
}

function textValue(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function formatDate(value: string | null): string {
  if (!value) return 'لا توجد جلسة مسجّلة بعد';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('ar-SA', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  }).format(date);
}

function getApiBase(): string {
  const configured = (window as Window & { __SAMEER_API_BASE__?: string }).__SAMEER_API_BASE__;
  return configured ? configured.replace(/\/$/, '') : '';
}

async function fetchJson(path: string, signal?: AbortSignal): Promise<unknown> {
  const response = await fetch(`${getApiBase()}${path}`, {
    signal,
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error(`${path} returned ${response.status}`);
  return response.json();
}

async function fetchOptional(path: string, signal?: AbortSignal): Promise<unknown> {
  try {
    return await fetchJson(path, signal);
  } catch {
    return null;
  }
}

function extractDimensions(stats: RecordValue, evaluation: RecordValue): QualityDimensions {
  const quality = asRecord(firstValue(evaluation, ['quality', 'dialogue_quality', 'evaluation']));
  const statsQuality = asRecord(firstValue(stats, ['quality', 'dialogue_quality', 'evaluation']));
  const pick = (keys: string[]) =>
    percentageValue(
      firstValue(quality, keys) ??
        firstValue(statsQuality, keys) ??
        firstValue(evaluation, keys) ??
        firstValue(stats, keys),
    );
  return {
    depth: pick(['depth', 'depth_score', 'عمق']),
    participation: pick(['participation', 'participation_score', 'مشاركة']),
    consistency: pick(['consistency', 'consistency_score', 'استمرارية']),
    balance: pick(['balance', 'balance_score', 'توازن']),
  };
}

function normalizeParticipants(source: RecordValue): Participant[] {
  const raw = asArray(
    firstValue(source, ['participants', 'last_session_participants', 'attendees', 'members']),
  );
  return raw.flatMap((entry) => {
    const participant = asRecord(entry);
    const name = textValue(firstValue(participant, ['name', 'display_name', 'member'])) ?? '';
    if (!name) return [];
    const rawStatus = String(firstValue(participant, ['status', 'participation_status']) ?? '').toLowerCase();
    const participated =
      Boolean(firstValue(participant, ['participated', 'spoke', 'active'])) ||
      ['participated', 'شارك', 'متحدث', 'active'].some((word) => rawStatus.includes(word));
    const silent =
      Boolean(firstValue(participant, ['silent', 'did_not_participate'])) ||
      ['silent', 'لم يشارك', 'صامت'].some((word) => rawStatus.includes(word));
    return [
      {
        name,
        status: participated ? 'participated' : silent ? 'silent' : 'attended',
        asked: Boolean(firstValue(participant, ['asked', 'samir_asked', 'prompted'])),
      },
    ];
  });
}

function normalizeWeekly(source: RecordValue): DashboardModel['weeklyQuality'] {
  const raw = asArray(firstValue(source, ['weekly_quality', 'quality_history', 'weekly', 'history']));
  return raw.flatMap((entry, index) => {
    const row = asRecord(entry);
    const score = percentageValue(firstValue(row, ['score', 'quality_score', 'quality', 'value', 'المؤشر']));
    if (score === null) return [];
    return [
      {
        label: textValue(firstValue(row, ['label', 'week', 'date', 'period'])) ?? `أسبوع ${index + 1}`,
        score,
      },
    ];
  });
}

function normalizeCategories(source: RecordValue): DashboardModel['categories'] {
  const raw = asRecord(firstValue(source, ['average_rating_per_category', 'category_scores', 'categories']));
  return Object.entries(raw)
    .map(([label, value]) => {
      const score = percentageValue(value);
      return score === null ? null : { label, score };
    })
    .filter((entry): entry is { label: string; score: number } => entry !== null)
    .sort((a, b) => CATEGORY_NAMES.indexOf(a.label) - CATEGORY_NAMES.indexOf(b.label));
}

function normalizeSessions(stats: RecordValue, intro: RecordValue): DashboardModel['recentSessions'] {
  const summaries = asArray(firstValue(intro, ['summaries', 'session_summaries', 'intros']));
  const raw = asArray(firstValue(stats, ['recent_sessions', 'sessions', 'recent']));
  const rows = raw.length ? raw : asArray(firstValue(stats, ['recent_topics'])).map((topic) => ({ topic }));
  return rows.flatMap((entry, index) => {
    const row = typeof entry === 'string' ? { topic: entry } : asRecord(entry);
    const topic = textValue(firstValue(row, ['topic', 'title', 'subject'])) ?? '';
    if (!topic) return [];
    const summaryRow = asRecord(summaries[index]);
    return [
      {
        topic,
        summary:
          textValue(firstValue(row, ['summary', 'ai_summary', 'generated_summary'])) ??
          textValue(firstValue(summaryRow, ['summary', 'text', 'intro'])),
        date: textValue(firstValue(row, ['date', 'created_at', 'session_at'])),
      },
    ];
  });
}

function buildModel(statsPayload: unknown, evaluationPayload: unknown, introPayload: unknown): DashboardModel {
  const stats = asRecord(statsPayload);
  const evaluation = asRecord(evaluationPayload);
  const intro = asRecord(introPayload);
  const dimensions = extractDimensions(stats, evaluation);
  const directScore = percentageValue(
    firstValue(evaluation, ['quality_score', 'qualityIndex', 'dialogue_quality_score', 'composite_score']) ??
      firstValue(stats, ['quality_score', 'qualityIndex', 'dialogue_quality_score', 'composite_score']),
  );
  const dimensionValues = Object.values(dimensions);
  const qualityScore =
    directScore ??
    (dimensionValues.every((value) => value !== null)
      ? Math.round((dimensionValues as number[]).reduce((sum, value) => sum + value, 0) / 4)
      : null);
  const privacy = asRecord(firstValue(stats, ['privacy', 'privacy_metrics', 'privacy_stats']));
  const sessions = normalizeSessions(stats, intro);
  return {
    familyName:
      textValue(firstValue(stats, ['family_name', 'familyName', 'household_name'])) ??
      textValue(firstValue(evaluation, ['family_name', 'familyName'])) ??
      '',
    lastSessionAt:
      textValue(firstValue(stats, ['last_session_at', 'lastSessionAt', 'last_session_date'])) ??
      sessions[0]?.date ??
      null,
    totalSessions: numberValue(stats, ['total_sessions', 'sessions_count', 'totalSessions']) ?? sessions.length,
    qualityScore,
    qualityDimensions: dimensions,
    participants: normalizeParticipants({ ...stats, ...asRecord(firstValue(stats, ['last_session'])) }),
    weeklyQuality: normalizeWeekly(stats),
    categories: normalizeCategories(stats),
    privacy: {
      voiceBytes: numberValue(privacy, ['voice_bytes_stored', 'stored_voice_bytes', 'voiceBytes']) ?? 0,
      images: numberValue(privacy, ['images_saved', 'stored_images', 'images']) ?? 0,
    },
    recentSessions: sessions,
  };
}

function qualityTone(score: number | null): {
  label: string;
  detail: string;
  color: string;
  Icon: typeof HeartHandshake;
} {
  if (score === null) {
    return {
      label: 'بانتظار أول تقييم',
      detail: 'سيحكم سمير على جودة الحوار تلقائياً بعد تسجيل جلسة.',
      color: MUTED,
      Icon: CircleHelp,
    };
  }
  if (score >= 80) {
    return {
      label: 'حوار عميق ومتوازن',
      detail: 'مستوى قوي في جودة المشاركة بين أفراد العائلة.',
      color: SUCCESS,
      Icon: HeartHandshake,
    };
  }
  if (score >= 60) {
    return {
      label: 'حوار يتقدّم',
      detail: 'هناك تفاعل جيد ومساحة واضحة للتحسن.',
      color: GOLD,
      Icon: Activity,
    };
  }
  return {
    label: 'بداية تحتاج دعوة',
    detail: 'يمكن لسمير توجيه السؤال التالي إلى صوت لم يظهر بعد.',
    color: WARNING,
    Icon: Sparkles,
  };
}

function EmptyPanel({ text }: { text: string }) {
  return (
    <div className="flex min-h-[180px] items-center justify-center rounded-xl border border-dashed border-[#d9d1c1] bg-[#fbf8f1] px-6 text-center">
      <div>
        <div className="mx-auto mb-3 flex h-9 w-9 items-center justify-center rounded-full bg-[#eee6d5] text-[#71808a]">
          <Clock3 size={17} strokeWidth={1.7} />
        </div>
        <p className="text-[13px] leading-6 text-[#71808a]">{text}</p>
      </div>
    </div>
  );
}

function QualityGauge({ score }: { score: number | null }) {
  const tone = qualityTone(score);
  const radius = 58;
  const circumference = 2 * Math.PI * radius;
  const dash = score === null ? 0 : (score / 100) * circumference;
  return (
    <div className="relative flex h-[164px] w-[164px] shrink-0 items-center justify-center">
      <svg className="-rotate-90" width="164" height="164" viewBox="0 0 164 164" aria-hidden="true">
        <circle cx="82" cy="82" r={radius} fill="none" stroke="rgba(246,241,231,.14)" strokeWidth="9" />
        <circle
          cx="82"
          cy="82"
          r={radius}
          fill="none"
          stroke={score === null ? '#71808a' : GOLD_LIGHT}
          strokeWidth="9"
          strokeLinecap="round"
          strokeDasharray={`${dash} ${circumference}`}
          className="transition-all duration-700"
        />
      </svg>
      <div className="absolute flex flex-col items-center">
        <span className="font-display text-[40px] leading-none text-[#f6f1e7]">{score ?? '—'}</span>
        <span className="mt-2 text-[10px] tracking-[0.12em] text-[#f6f1e7]/55">من ١٠٠</span>
      </div>
    </div>
  );
}

function ParticipantRow({ participant }: { participant: Participant }) {
  const status =
    participant.status === 'participated'
      ? { label: 'شارك فعلياً', color: SUCCESS, bg: '#e5eee5', Icon: Check }
      : participant.status === 'silent'
        ? { label: 'لم يشارك', color: DANGER, bg: '#f2e3df', Icon: MicOff }
        : { label: 'حضر فقط', color: WARNING, bg: '#f0e7d1', Icon: UserRound };
  const Icon = status.Icon;
  return (
    <div className="flex items-center gap-3 border-b border-[#e7dfd0] py-3 last:border-0">
      <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[#e5e0d4] text-[#173d5c]">
        <UserRound size={16} strokeWidth={1.65} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13px] font-semibold text-[#203545]">{participant.name}</div>
        {participant.asked && (
          <div className="mt-0.5 flex items-center gap-1 text-[10px] text-[#9d7731]">
            <MessageCircle size={11} /> وجّه سمير سؤالاً لهذا الفرد
          </div>
        )}
      </div>
      <span className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-[10px] font-semibold" style={{ color: status.color, background: status.bg }}>
        <Icon size={11} /> {status.label}
      </span>
    </div>
  );
}

function App() {
  const [model, setModel] = useState<DashboardModel>(EMPTY_MODEL);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [familyName, setFamilyName] = useState('');
  const [presentationMode, setPresentationMode] = useState(false);
  const [mobileMenu, setMobileMenu] = useState(false);

  const loadData = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true);
    else setLoading(true);
    try {
      const [stats, evaluation, intro] = await Promise.all([
        fetchJson('/stats'),
        fetchOptional('/evaluate_session'),
        fetchOptional('/session_intro'),
      ]);
      setModel(buildModel(stats, evaluation, intro));
      setError(null);
      setLastUpdated(new Date());
    } catch {
      setError('تعذّر الوصول إلى بيانات سمير. ستُعاد المحاولة تلقائياً.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    const stored = window.localStorage.getItem('sameer_family_name');
    if (stored) setFamilyName(stored);
    void loadData();
    const interval = window.setInterval(() => void loadData(true), 30000);
    return () => window.clearInterval(interval);
  }, [loadData]);

  useEffect(() => {
    if (!familyName && model.familyName) setFamilyName(model.familyName);
  }, [familyName, model.familyName]);

  const activeName = familyName || 'اسم العائلة';
  const tone = qualityTone(model.qualityScore);
  const ToneIcon = tone.Icon;
  const attendedCount = model.participants.length;
  const participatedCount = model.participants.filter((member) => member.status === 'participated').length;
  const silentCount = model.participants.filter((member) => member.status === 'silent').length;
  const dimensionRows = [
    ['العمق', model.qualityDimensions.depth],
    ['المشاركة', model.qualityDimensions.participation],
    ['الاستمرارية', model.qualityDimensions.consistency],
    ['التوازن', model.qualityDimensions.balance],
  ] as const;

  const saveFamilyName = () => {
    const trimmed = familyName.trim();
    if (trimmed) window.localStorage.setItem('sameer_family_name', trimmed);
    else window.localStorage.removeItem('sameer_family_name');
  };

  const scrollTo = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setMobileMenu(false);
  };

  const navItems = [
    { label: 'نظرة عامة', icon: LayoutDashboard, id: 'quality' },
    { label: 'المشاركة', icon: UsersRound, id: 'participation' },
    { label: 'التطور', icon: LineChartIcon, id: 'timeline' },
    { label: 'المواضيع', icon: MessageCircle, id: 'sessions' },
  ];

  return (
    <div dir="rtl" className={`min-h-screen bg-[#f6f1e7] text-[#203545] ${presentationMode ? 'presentation-mode' : ''}`}>
      <style dangerouslySetInnerHTML={{ __html: `
        @import url('https://fonts.googleapis.com/css2?family=Cairo:wght@500;600;700;800&family=IBM+Plex+Sans+Arabic:wght@400;500;600;700&display=swap');
        :root { color-scheme: light; }
        body { margin: 0; background: #f6f1e7; }
        .sameer-display { font-family: 'Cairo', sans-serif; }
        .sameer-body { font-family: 'IBM Plex Sans Arabic', sans-serif; }
        .sameer-shell { font-family: 'IBM Plex Sans Arabic', sans-serif; }
        .sameer-grid { background-image: linear-gradient(rgba(13,41,66,.045) 1px, transparent 1px), linear-gradient(90deg, rgba(13,41,66,.045) 1px, transparent 1px); background-size: 28px 28px; }
        .sameer-panel { box-shadow: 0 12px 32px rgba(13,41,66,.055); }
        .presentation-mode .sameer-sidebar { display: none; }
        .presentation-mode .sameer-main { max-width: 1480px; margin: 0 auto; }
        .presentation-mode .sameer-main-content { padding-top: 44px; }
        .presentation-mode .sameer-quality-score { transform: scale(1.08); transform-origin: center right; }
        .presentation-mode .sameer-secondary { display: none; }
        .sameer-scroll::-webkit-scrollbar { width: 8px; height: 8px; }
        .sameer-scroll::-webkit-scrollbar-thumb { background: #c9b98e; border-radius: 99px; }
        @media (prefers-reduced-motion: reduce) {
          *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; animation-duration: .01ms !important; }
        }
      `}} />

      <div className="sameer-shell flex min-h-screen">
        {!presentationMode && (
          <aside className="sameer-sidebar sticky top-0 z-30 hidden h-screen w-[254px] shrink-0 flex-col bg-[#0d2942] px-6 py-7 text-[#f6f1e7] lg:flex">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-[#ead7a5]/45 bg-[#173d5c] text-[#ead7a5]">
                <span className="sameer-display text-[18px] font-bold">س</span>
              </div>
              <div>
                <div className="sameer-display text-[20px] font-bold leading-none">سمير</div>
                <div className="mt-1 text-[10px] tracking-[0.16em] text-[#f6f1e7]/48">حوار يجمعنا</div>
              </div>
            </div>
            <div className="mt-12 mb-3 text-[10px] font-semibold tracking-[0.14em] text-[#ead7a5]/65">مساحة العائلة</div>
            <nav className="flex flex-col gap-1">
              {navItems.map(({ label, icon: Icon, id }) => (
                <button
                  key={id}
                  onClick={() => scrollTo(id)}
                  className="flex items-center gap-3 rounded-xl px-3 py-3 text-right text-[13px] text-[#f6f1e7]/70 transition hover:bg-[#f6f1e7]/10 hover:text-[#f6f1e7]"
                >
                  <Icon size={17} strokeWidth={1.65} />
                  <span>{label}</span>
                </button>
              ))}
            </nav>
            <div className="mt-auto rounded-2xl border border-[#ead7a5]/18 bg-[#173d5c] p-4">
              <div className="flex items-center gap-2 text-[#ead7a5]">
                <ShieldCheck size={16} />
                <span className="text-[12px] font-semibold">خصوصية افتراضية</span>
              </div>
              <p className="mt-2 text-[11px] leading-6 text-[#f6f1e7]/58">
                لا كاميرا، ولا تسجيل صوتي دائم. ما يبقى في اللوحة هو مؤشرات الحوار فقط.
              </p>
            </div>
          </aside>
        )}

        <main className="sameer-main min-w-0 flex-1">
          <header className="sticky top-0 z-20 border-b border-[#d9d1c1] bg-[#f6f1e7]/92 backdrop-blur-md">
            <div className="flex min-h-[76px] items-center gap-3 px-5 lg:px-10">
              {!presentationMode && (
                <button
                  className="flex h-10 w-10 items-center justify-center rounded-xl border border-[#d9d1c1] bg-[#fbf8f1] lg:hidden"
                  onClick={() => setMobileMenu(!mobileMenu)}
                  aria-label={mobileMenu ? 'إغلاق القائمة' : 'فتح القائمة'}
                >
                  {mobileMenu ? <X size={18} /> : <Menu size={18} />}
                </button>
              )}
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#0d2942] text-[#ead7a5] lg:hidden">
                  <span className="sameer-display text-[18px] font-bold">س</span>
                </div>
                <div>
                  <div className="mb-0.5 flex items-center gap-2 text-[10px] font-semibold tracking-[0.08em] text-[#71808a]">
                    <span>لوحة سمير</span>
                    <span className="h-1 w-1 rounded-full bg-[#c59c45]" />
                    <span>متابعة حية</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <input
                      value={familyName}
                      onChange={(event) => setFamilyName(event.target.value)}
                      onBlur={saveFamilyName}
                      placeholder="اسم العائلة"
                      aria-label="اسم العائلة"
                      className="sameer-display w-[130px] border-0 bg-transparent p-0 text-[17px] font-bold text-[#0d2942] outline-none placeholder:text-[#0d2942]/35"
                    />
                    <Settings2 size={13} className="text-[#71808a]" />
                  </div>
                </div>
              </div>
              <div className="mr-auto flex items-center gap-2">
                <div className="hidden items-center gap-2 rounded-full border border-[#d9d1c1] bg-[#fbf8f1] px-3 py-2 text-[11px] text-[#71808a] md:flex">
                  <Clock3 size={13} />
                  <span>آخر جلسة: {formatDate(model.lastSessionAt)}</span>
                </div>
                <button className="relative flex h-10 w-10 items-center justify-center rounded-full border border-[#d9d1c1] bg-[#fbf8f1]" aria-label="الإشعارات">
                  <Bell size={16} strokeWidth={1.7} />
                  {model.participants.some((member) => member.asked) && <span className="absolute right-2 top-2 h-2 w-2 rounded-full bg-[#c59c45] ring-2 ring-[#f6f1e7]" />}
                </button>
                <button
                  onClick={() => setPresentationMode(!presentationMode)}
                  className={`hidden items-center gap-2 rounded-full px-4 py-2.5 text-[12px] font-semibold transition sm:flex ${presentationMode ? 'bg-[#c59c45] text-[#0d2942]' : 'bg-[#0d2942] text-[#f6f1e7] hover:bg-[#173d5c]'}`}
                >
                  <MonitorPlay size={15} />
                  {presentationMode ? 'إنهاء وضع العرض' : 'وضع العرض'}
                </button>
              </div>
            </div>
            {mobileMenu && !presentationMode && (
              <nav className="border-t border-[#d9d1c1] bg-[#fbf8f1] px-5 py-3 lg:hidden">
                <div className="grid grid-cols-2 gap-2">
                  {navItems.map(({ label, icon: Icon, id }) => (
                    <button key={id} onClick={() => scrollTo(id)} className="flex items-center gap-2 rounded-xl px-3 py-2 text-right text-[12px] text-[#203545] hover:bg-[#eee6d5]">
                      <Icon size={15} /> {label}
                    </button>
                  ))}
                  <button onClick={() => { setPresentationMode(true); setMobileMenu(false); }} className="flex items-center gap-2 rounded-xl px-3 py-2 text-right text-[12px] text-[#203545] hover:bg-[#eee6d5]">
                    <MonitorPlay size={15} /> وضع العرض
                  </button>
                </div>
              </nav>
            )}
          </header>

          <div className="sameer-main-content sameer-grid min-h-[calc(100vh-76px)] px-5 py-6 lg:px-10 lg:py-8">
            <div className="mx-auto max-w-[1260px]">
              <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
                <div>
                  <div className="mb-1 text-[11px] font-semibold text-[#c59c45]">حكم سمير المستقل</div>
                  <h1 className="sameer-display text-[27px] font-bold leading-tight text-[#0d2942] sm:text-[33px]">كيف كان حوارنا؟</h1>
                  <p className="mt-2 max-w-[630px] text-[13px] leading-7 text-[#71808a]">
                    مؤشرات مركّبة تساعد العائلة على رؤية أثر الجلسات، من دون تسجيل ما قيل داخلها.
                  </p>
                </div>
                <button
                  onClick={() => void loadData(true)}
                  disabled={refreshing}
                  className="flex items-center gap-2 rounded-full border border-[#d9d1c1] bg-[#fbf8f1] px-4 py-2.5 text-[12px] font-semibold text-[#0d2942] transition hover:border-[#c59c45] disabled:opacity-60"
                >
                  <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
                  تحديث البيانات
                </button>
              </div>

              {error && (
                <div className="mb-5 flex items-center gap-3 rounded-2xl border border-[#e0c7bd] bg-[#f7ebe5] px-4 py-3 text-[12px] text-[#824f4f]">
                  <WifiOff size={16} /> {error}
                </div>
              )}

              <section id="quality" className="sameer-panel overflow-hidden rounded-3xl bg-[#0d2942] text-[#f6f1e7]">
                <div className="grid gap-7 p-6 sm:p-8 lg:grid-cols-[1.35fr_.85fr] lg:items-center lg:p-10">
                  <div className="sameer-quality-score">
                    <div className="mb-3 flex items-center gap-2 text-[11px] font-semibold text-[#ead7a5]">
                      <Gauge size={15} /> مؤشر جودة الحوار المركّب
                    </div>
                    <div className="flex flex-wrap items-center gap-6">
                      <QualityGauge score={model.qualityScore} />
                      <div className="min-w-[200px] flex-1">
                        <div className="mb-2 flex items-center gap-2">
                          <ToneIcon size={19} color={tone.color} />
                          <h2 className="sameer-display text-[23px] font-bold">{tone.label}</h2>
                        </div>
                        <p className="max-w-[420px] text-[13px] leading-7 text-[#f6f1e7]/62">{tone.detail}</p>
                        <div className="mt-5 flex items-center gap-2 text-[11px] text-[#f6f1e7]/45">
                          <Activity size={13} />
                          <span>{model.totalSessions ? `${model.totalSessions} جلسة في الذاكرة` : 'لا توجد جلسات كافية للحساب بعد'}</span>
                        </div>
                      </div>
                    </div>
                  </div>
                  <div className="rounded-2xl border border-[#f6f1e7]/12 bg-[#173d5c]/65 p-5">
                    <div className="mb-4 flex items-center justify-between">
                      <span className="text-[12px] font-semibold text-[#f6f1e7]/78">مكوّنات الحكم</span>
                      <span className="text-[10px] text-[#ead7a5]">٤ أبعاد</span>
                    </div>
                    <div className="space-y-4">
                      {dimensionRows.map(([label, value]) => (
                        <div key={label}>
                          <div className="mb-1.5 flex items-center justify-between text-[11px]">
                            <span className="text-[#f6f1e7]/65">{label}</span>
                            <span className="font-semibold text-[#ead7a5]">{value === null ? '—' : `${value}%`}</span>
                          </div>
                          <div className="h-1.5 overflow-hidden rounded-full bg-[#f6f1e7]/12">
                            <div className="h-full rounded-full bg-[#ead7a5] transition-all duration-700" style={{ width: `${value ?? 0}%` }} />
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </section>

              <div className="mt-5 grid gap-5 lg:grid-cols-2">
                <section id="participation" className="sameer-panel rounded-3xl border border-[#d9d1c1] bg-[#fbf8f1] p-5 sm:p-6">
                  <div className="mb-4 flex items-start justify-between gap-4">
                    <div>
                      <div className="mb-1 text-[11px] font-semibold text-[#c59c45]">من كان معنا؟</div>
                      <h2 className="sameer-display text-[21px] font-bold text-[#0d2942]">رصد المشاركة بالاسم</h2>
                    </div>
                    <div className="flex h-10 w-10 items-center justify-center rounded-full bg-[#eee6d5] text-[#0d2942]"><UsersRound size={17} /></div>
                  </div>
                  {loading ? (
                    <div className="flex min-h-[180px] items-center justify-center text-[#71808a]"><LoaderCircle className="animate-spin" size={20} /></div>
                  ) : model.participants.length ? (
                    <>
                      <div className="mb-3 grid grid-cols-3 gap-2">
                        <div className="rounded-xl bg-[#eee6d5] px-3 py-2"><strong className="sameer-display block text-[20px] text-[#0d2942]">{attendedCount}</strong><span className="text-[10px] text-[#71808a]">حضروا</span></div>
                        <div className="rounded-xl bg-[#e5eee5] px-3 py-2"><strong className="sameer-display block text-[20px] text-[#52715d]">{participatedCount}</strong><span className="text-[10px] text-[#71808a]">شاركوا</span></div>
                        <div className="rounded-xl bg-[#f2e3df] px-3 py-2"><strong className="sameer-display block text-[20px] text-[#9b5e5e]">{silentCount}</strong><span className="text-[10px] text-[#71808a]">لم يشاركوا</span></div>
                      </div>
                      <div className="sameer-scroll max-h-[250px] overflow-y-auto">
                        {model.participants.map((participant) => <ParticipantRow key={participant.name} participant={participant} />)}
                      </div>
                    </>
                  ) : (
                    <EmptyPanel text="ستظهر أسماء الحاضرين والمشاركين بعد أن يرسل سمير بيانات الجلسة الأخيرة." />
                  )}
                </section>

                <section id="privacy" className="sameer-panel rounded-3xl border border-[#d9d1c1] bg-[#fbf8f1] p-5 sm:p-6">
                  <div className="mb-5 flex items-start justify-between gap-4">
                    <div>
                      <div className="mb-1 text-[11px] font-semibold text-[#c59c45]">خصوصية ملموسة</div>
                      <h2 className="sameer-display text-[21px] font-bold text-[#0d2942]">ما الذي لا يحتفظ به سمير؟</h2>
                    </div>
                    <div className="flex h-10 w-10 items-center justify-center rounded-full bg-[#e5eee5] text-[#52715d]"><LockKeyhole size={17} /></div>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="rounded-2xl border border-[#d9d1c1] bg-[#f6f1e7] p-4">
                      <div className="mb-3 flex items-center justify-between text-[#52715d]"><MicOff size={18} /><span className="rounded-full bg-[#e5eee5] px-2 py-1 text-[10px] font-semibold">هذا الشهر</span></div>
                      <div className="sameer-display text-[26px] font-bold text-[#0d2942]">{model.privacy.voiceBytes} <span className="text-[13px] font-semibold">بايت</span></div>
                      <p className="mt-1 text-[11px] text-[#71808a]">صوت مخزّن</p>
                    </div>
                    <div className="rounded-2xl border border-[#d9d1c1] bg-[#f6f1e7] p-4">
                      <div className="mb-3 flex items-center justify-between text-[#52715d]"><ShieldCheck size={18} /><span className="rounded-full bg-[#e5eee5] px-2 py-1 text-[10px] font-semibold">هذا الشهر</span></div>
                      <div className="sameer-display text-[26px] font-bold text-[#0d2942]">{model.privacy.images} <span className="text-[13px] font-semibold">صور</span></div>
                      <p className="mt-1 text-[11px] text-[#71808a]">صور محفوظة</p>
                    </div>
                  </div>
                  <div className="mt-4 flex items-start gap-2 text-[11px] leading-6 text-[#71808a]">
                    <ShieldCheck size={14} className="mt-1 shrink-0 text-[#52715d]" />
                    <span>تُحفظ مؤشرات الجلسة فقط. لا يظهر في هذه اللوحة نص حرفي من الحديث العائلي.</span>
                  </div>
                </section>
              </div>

              <div className="sameer-secondary mt-5 grid gap-5 xl:grid-cols-[1.3fr_.7fr]">
                <section id="timeline" className="sameer-panel rounded-3xl border border-[#d9d1c1] bg-[#fbf8f1] p-5 sm:p-6">
                  <div className="mb-4 flex items-start justify-between">
                    <div>
                      <div className="mb-1 text-[11px] font-semibold text-[#c59c45]">الأثر مع الوقت</div>
                      <h2 className="sameer-display text-[21px] font-bold text-[#0d2942]">تطور جودة الحوار</h2>
                      <p className="mt-1 text-[11px] text-[#71808a]">متوسط الحكم المركّب لكل أسبوع مسجّل.</p>
                    </div>
                    <LineChartIcon size={19} className="text-[#c59c45]" />
                  </div>
                  {model.weeklyQuality.length ? (
                    <div className="h-[260px] w-full" dir="ltr">
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={model.weeklyQuality} margin={{ top: 12, right: 8, left: -18, bottom: 4 }}>
                          <CartesianGrid stroke="#d9d1c1" strokeDasharray="2 6" vertical={false} />
                          <XAxis dataKey="label" axisLine={false} tickLine={false} tick={{ fill: MUTED, fontSize: 10 }} />
                          <YAxis domain={[0, 100]} axisLine={false} tickLine={false} tick={{ fill: MUTED, fontSize: 10 }} width={28} />
                          <Tooltip contentStyle={{ background: NAVY, border: 'none', borderRadius: 12, color: PAPER, fontFamily: 'IBM Plex Sans Arabic' }} formatter={(value) => [`${value}%`, 'جودة الحوار']} />
                          <Line type="monotone" dataKey="score" stroke={NAVY_2} strokeWidth={3} dot={{ r: 4, fill: GOLD, stroke: NAVY, strokeWidth: 2 }} activeDot={{ r: 6, fill: GOLD, stroke: NAVY, strokeWidth: 2 }} />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  ) : <EmptyPanel text="سيظهر المنحنى بعد تسجيل أكثر من أسبوع من الجلسات." />}
                </section>

                <section className="sameer-panel rounded-3xl border border-[#d9d1c1] bg-[#fbf8f1] p-5 sm:p-6">
                  <div className="mb-4 flex items-start justify-between">
                    <div>
                      <div className="mb-1 text-[11px] font-semibold text-[#c59c45]">ما الذي يحرّك الحوار؟</div>
                      <h2 className="sameer-display text-[21px] font-bold text-[#0d2942]">توزيع الفئات</h2>
                    </div>
                    <BarChart3 size={19} className="text-[#c59c45]" />
                  </div>
                  {model.categories.length ? (
                    <div className="h-[260px] w-full" dir="ltr">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={model.categories} margin={{ top: 12, right: 6, left: -18, bottom: 4 }}>
                          <CartesianGrid stroke="#d9d1c1" strokeDasharray="2 6" vertical={false} />
                          <XAxis dataKey="label" axisLine={false} tickLine={false} tick={{ fill: MUTED, fontSize: 9 }} />
                          <YAxis domain={[0, 100]} axisLine={false} tickLine={false} tick={{ fill: MUTED, fontSize: 10 }} width={28} />
                          <Tooltip contentStyle={{ background: NAVY, border: 'none', borderRadius: 12, color: PAPER, fontFamily: 'IBM Plex Sans Arabic' }} formatter={(value) => [`${value}%`, 'التفاعل']} />
                          <Bar dataKey="score" radius={[6, 6, 0, 0]}>
                            {model.categories.map((entry, index) => <Cell key={entry.label} fill={CATEGORY_COLORS[index % CATEGORY_COLORS.length]} />)}
                          </Bar>
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  ) : <EmptyPanel text="ستُقارن الفئات بعد أن تسجّل الجلسات تقييمات قابلة للمقارنة." />}
                </section>
              </div>

              <section id="sessions" className="sameer-secondary sameer-panel mt-5 rounded-3xl border border-[#d9d1c1] bg-[#fbf8f1] p-5 sm:p-6">
                <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <div className="mb-1 text-[11px] font-semibold text-[#c59c45]">ذاكرة بلا اقتباس</div>
                    <h2 className="sameer-display text-[21px] font-bold text-[#0d2942]">آخر المواضيع وملخص سمير</h2>
                    <p className="mt-1 text-[11px] text-[#71808a]">ملخص موجز يولّده endpoint الجلسة، لا نص حرفي من الحديث.</p>
                  </div>
                  <div className="flex items-center gap-2 rounded-full bg-[#eee6d5] px-3 py-2 text-[10px] font-semibold text-[#71808a]">
                    <Sparkles size={13} className="text-[#c59c45]" /> ملخصات تحافظ على الخصوصية
                  </div>
                </div>
                {model.recentSessions.length ? (
                  <div className="grid gap-3 md:grid-cols-2">
                    {model.recentSessions.map((session, index) => (
                      <article key={`${session.topic}-${index}`} className="rounded-2xl border border-[#e0d8c8] bg-[#f6f1e7] p-4">
                        <div className="flex items-center justify-between gap-3">
                          <h3 className="text-[14px] font-bold text-[#0d2942]">{session.topic}</h3>
                          <span className="text-[10px] text-[#71808a]">{formatDate(session.date)}</span>
                        </div>
                        <p className="mt-3 text-[12px] leading-6 text-[#71808a]">
                          {session.summary ?? 'ملخص سمير لهذه الجلسة قيد الإعداد.'}
                        </p>
                      </article>
                    ))}
                  </div>
                ) : <EmptyPanel text="ستظهر هنا آخر الموضوعات مع ملخصات قصيرة يولّدها سمير بعد تفعيل جلسات الحوار." />}
              </section>

              <footer className="flex flex-wrap items-center justify-between gap-3 px-1 pb-4 pt-8 text-[10px] text-[#71808a]">
                <span>سمير — جائزة نورة الملاحي للابتكار الاجتماعي التقني</span>
                <span>{lastUpdated ? `آخر تحديث ${lastUpdated.toLocaleTimeString('ar-SA', { hour: '2-digit', minute: '2-digit' })}` : 'تحديث تلقائي كل ٣٠ ثانية'}</span>
              </footer>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}

export default App;