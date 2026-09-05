import { useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'

type PublicConfig = {
  public_phone_enabled: boolean
  public_phone_number: string | null
}

type ChatResponse = {
  session_id: number
  response: string
  needs_handoff: boolean
  handoff_reason: string | null
}

type DashboardSessionSummary = {
  session_id: number
  started_at: string
  ended_at: string | null
  channel: string
  status: string
  customer_name: string | null
  summary: string
  action_count: number
  message_count: number
  needs_handoff: boolean
  handoff_reason: string | null
}

type DashboardAppointment = {
  job_id: string
  customer_name: string | null
  description: string | null
  address: string | null
  technicians: string[]
  scheduled_start: string | null
  scheduled_end: string | null
  effective_start_at: string | null
  effective_end_at: string | null
  original_start_at?: string | null
  original_end_at?: string | null
  status: string | null
  has_schedule_override?: boolean
  contact_summary?: string | null
  weather: {
    temperature_f: number
    precipitation_probability: number
    wind_mph: number
    condition: string
    risk_indicator?: string | null
  } | null
}

type ScheduleChange = {
  change_id: number
  job_id: string
  customer_name: string | null
  address: string | null
  description: string | null
  original_start_at: string | null
  original_end_at: string | null
  new_start_at: string | null
  new_end_at: string | null
  reason: string | null
  status: string
  created_at: string
  agent_session_id: number | null
  channel: string | null
  changed_by: string
}

type AppointmentListResponse = {
  items: DashboardAppointment[]
  total: number
  limit: number
  offset: number
}

type ScheduleChangeListResponse = {
  items: ScheduleChange[]
  total: number
  limit: number
  offset: number
}

type DashboardSummary = {
  customers: number
  jobs: number
  ai_conversations: number
  active_sessions: number
  human_handoffs: number
  upcoming_appointments: number
  open_invoices: number
  outstanding_balance: number
  recent_sessions: DashboardSessionSummary[]
  appointments: DashboardAppointment[]
  recent_schedule_changes: ScheduleChange[]
  outstanding_accounts: Array<{
    invoice_id: string
    customer_name: string | null
    due_amount: number
    status: string | null
  }>
}

type DashboardAction = {
  id: number
  timestamp: string
  display_label: string
  detail?: string | null
  action_type: string
  status: string | null
  latency_ms: number | null
  arguments: Record<string, unknown>
}

type DashboardMessage = {
  role: string
  content: string
}

type DashboardSessionDetail = {
  session: DashboardSessionSummary
  conversation: DashboardMessage[]
  actions: DashboardAction[]
}

type ComposerMessage = {
  role: 'user' | 'assistant'
  content: string
}

type RecognitionState = 'idle' | 'listening' | 'thinking' | 'speaking'
type AppointmentWindow = '24h' | '7d' | '30d'

type Route =
  | 'chat'
  | 'dashboard'
  | 'appointments'
  | 'sessions'
  | 'scheduleChanges'
  | 'handoffs'
  | 'sessionDetail'

type SpeechRecognitionCtor = new () => {
  continuous: boolean
  interimResults: boolean
  lang: string
  onresult: ((event: any) => void) | null
  onerror: ((event: any) => void) | null
  onend: (() => void) | null
  start: () => void
  stop: () => void
}

declare global {
  interface Window {
    SpeechRecognition?: SpeechRecognitionCtor
    webkitSpeechRecognition?: SpeechRecognitionCtor
  }
}

const MIAMI_TIME_ZONE = 'America/New_York'

const NAV_ITEMS = [
  { route: 'chat', label: 'Assistant', href: '/chat' },
  { route: 'dashboard', label: 'Dashboard', href: '/dashboard' },
  { route: 'appointments', label: 'Appointments', href: '/dashboard/appointments' },
  { route: 'sessions', label: 'Conversations', href: '/dashboard/sessions' },
  { route: 'scheduleChanges', label: 'Schedule Changes', href: '/dashboard/schedule-changes' },
  { route: 'handoffs', label: 'Handoffs', href: '/dashboard/handoffs' },
] as const

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
    ...init,
  })
  if (!response.ok) {
    const text = await response.text()
    throw new Error(text || `Request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

function formatDateTime(value: string | null): string {
  if (!value) {
    return '-'
  }
  return new Date(value).toLocaleString([], {
    timeZone: MIAMI_TIME_ZONE,
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function formatCompact(value: string | null): string {
  if (!value) {
    return '-'
  }
  return new Date(value).toLocaleString([], {
    timeZone: MIAMI_TIME_ZONE,
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function formatTime(value: string | null): string {
  if (!value) {
    return '-'
  }
  return new Date(value).toLocaleTimeString([], {
    timeZone: MIAMI_TIME_ZONE,
    hour: 'numeric',
    minute: '2-digit',
  })
}

function formatWindow(start: string | null, end: string | null): string {
  if (!start) {
    return '-'
  }
  if (!end) {
    return formatCompact(start)
  }
  return `${formatCompact(start)} - ${formatTime(end)}`
}

function parseRoute(pathname: string): { route: Route; sessionId: number | null } {
  const detailMatch = pathname.match(/^\/dashboard\/sessions\/(\d+)$/)
  if (detailMatch) {
    return { route: 'sessionDetail', sessionId: Number(detailMatch[1]) }
  }
  if (pathname === '/dashboard') {
    return { route: 'dashboard', sessionId: null }
  }
  if (pathname === '/dashboard/appointments') {
    return { route: 'appointments', sessionId: null }
  }
  if (pathname === '/dashboard/sessions') {
    return { route: 'sessions', sessionId: null }
  }
  if (pathname === '/dashboard/schedule-changes') {
    return { route: 'scheduleChanges', sessionId: null }
  }
  if (pathname === '/dashboard/handoffs') {
    return { route: 'handoffs', sessionId: null }
  }
  return { route: 'chat', sessionId: null }
}

function addHours(base: Date, hours: number): Date {
  return new Date(base.getTime() + hours * 60 * 60 * 1000)
}

function toQuery(params: Record<string, string | number | null | undefined>): string {
  const qs = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      qs.set(key, String(value))
    }
  })
  const rendered = qs.toString()
  return rendered ? `?${rendered}` : ''
}

function App() {
  const [routeState, setRouteState] = useState(() => parseRoute(window.location.pathname))
  const [publicConfig, setPublicConfig] = useState<PublicConfig | null>(null)
  const [chatSessionId, setChatSessionId] = useState<number | null>(null)
  const [messages, setMessages] = useState<ComposerMessage[]>([])
  const [composer, setComposer] = useState('')
  const [loadingChat, setLoadingChat] = useState(false)
  const [chatError, setChatError] = useState<string | null>(null)
  const [chatActions, setChatActions] = useState<DashboardAction[]>([])
  const [chatSummary, setChatSummary] = useState<DashboardSessionSummary | null>(null)
  const [recognitionState, setRecognitionState] = useState<RecognitionState>('idle')
  const [voiceEnabled, setVoiceEnabled] = useState(false)

  const [dashboardSummary, setDashboardSummary] = useState<DashboardSummary | null>(null)
  const [dashboardSessions, setDashboardSessions] = useState<DashboardSessionSummary[]>([])
  const [dashboardHandoffs, setDashboardHandoffs] = useState<DashboardSessionSummary[]>([])
  const [sessionDetail, setSessionDetail] = useState<DashboardSessionDetail | null>(null)
  const [dashboardLoading, setDashboardLoading] = useState(false)
  const [dashboardError, setDashboardError] = useState<string | null>(null)

  const [dashboardSearch, setDashboardSearch] = useState('')
  const [dashboardSearchDebounced, setDashboardSearchDebounced] = useState('')
  const [dashboardPreviewAppointments, setDashboardPreviewAppointments] = useState<DashboardAppointment[]>([])

  const [appointmentsSearch, setAppointmentsSearch] = useState('')
  const [appointmentsSearchDebounced, setAppointmentsSearchDebounced] = useState('')
  const [appointmentsWindow, setAppointmentsWindow] = useState<AppointmentWindow>('7d')
  const [appointmentsPage, setAppointmentsPage] = useState<AppointmentListResponse>({
    items: [],
    total: 0,
    limit: 20,
    offset: 0,
  })

  const [scheduleChangesSearch, setScheduleChangesSearch] = useState('')
  const [scheduleChangesSearchDebounced, setScheduleChangesSearchDebounced] = useState('')
  const [scheduleChangesPage, setScheduleChangesPage] = useState<ScheduleChangeListResponse>({
    items: [],
    total: 0,
    limit: 20,
    offset: 0,
  })

  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const recognitionRef = useRef<InstanceType<SpeechRecognitionCtor> | null>(null)

  const route = routeState.route
  const selectedSessionId = routeState.sessionId
  const speechSupported = useMemo(
    () => Boolean(window.SpeechRecognition || window.webkitSpeechRecognition),
    [],
  )

  useEffect(() => {
    const onPop = () => setRouteState(parseRoute(window.location.pathname))
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    void api<PublicConfig>('/api/public/config')
      .then(setPublicConfig)
      .catch(() => setPublicConfig({ public_phone_enabled: false, public_phone_number: null }))
  }, [])

  useEffect(() => {
    const timeoutId = window.setTimeout(() => setDashboardSearchDebounced(dashboardSearch.trim()), 320)
    return () => window.clearTimeout(timeoutId)
  }, [dashboardSearch])

  useEffect(() => {
    const timeoutId = window.setTimeout(() => setAppointmentsSearchDebounced(appointmentsSearch.trim()), 320)
    return () => window.clearTimeout(timeoutId)
  }, [appointmentsSearch])

  useEffect(() => {
    const timeoutId = window.setTimeout(() => setScheduleChangesSearchDebounced(scheduleChangesSearch.trim()), 320)
    return () => window.clearTimeout(timeoutId)
  }, [scheduleChangesSearch])

  useEffect(() => {
    if (route === 'dashboard') {
      setDashboardLoading(true)
      setDashboardError(null)
      void api<DashboardSummary>('/api/dashboard/summary')
        .then(setDashboardSummary)
        .catch((error: Error) => setDashboardError(error.message))
        .finally(() => setDashboardLoading(false))
    }
  }, [route])

  useEffect(() => {
    if (route === 'dashboard') {
      const query = toQuery({
        q: dashboardSearchDebounced,
        limit: 8,
        offset: 0,
      })
      void api<AppointmentListResponse>(`/api/dashboard/appointments${query}`)
        .then((payload) => setDashboardPreviewAppointments(payload.items))
        .catch(() => setDashboardPreviewAppointments([]))
    }
  }, [route, dashboardSearchDebounced])

  useEffect(() => {
    if (route === 'sessions') {
      setDashboardLoading(true)
      setDashboardError(null)
      void api<DashboardSessionSummary[]>('/api/dashboard/sessions?limit=80')
        .then(setDashboardSessions)
        .catch((error: Error) => setDashboardError(error.message))
        .finally(() => setDashboardLoading(false))
    }
  }, [route])

  useEffect(() => {
    if (route === 'handoffs') {
      setDashboardLoading(true)
      setDashboardError(null)
      void api<DashboardSessionSummary[]>('/api/dashboard/handoffs?limit=80')
        .then(setDashboardHandoffs)
        .catch((error: Error) => setDashboardError(error.message))
        .finally(() => setDashboardLoading(false))
    }
  }, [route])

  useEffect(() => {
    if (route === 'sessionDetail' && selectedSessionId) {
      setDashboardLoading(true)
      setDashboardError(null)
      void api<DashboardSessionDetail>(`/api/dashboard/sessions/${selectedSessionId}`)
        .then(setSessionDetail)
        .catch((error: Error) => setDashboardError(error.message))
        .finally(() => setDashboardLoading(false))
    }
  }, [route, selectedSessionId])

  useEffect(() => {
    if (route !== 'appointments') {
      return
    }

    const now = new Date()
    const toAt =
      appointmentsWindow === '24h'
        ? addHours(now, 24)
        : appointmentsWindow === '7d'
          ? addHours(now, 24 * 7)
          : addHours(now, 24 * 30)

    const query = toQuery({
      q: appointmentsSearchDebounced,
      from: now.toISOString(),
      to: toAt.toISOString(),
      limit: appointmentsPage.limit,
      offset: appointmentsPage.offset,
    })

    setDashboardLoading(true)
    setDashboardError(null)
    void api<AppointmentListResponse>(`/api/dashboard/appointments${query}`)
      .then(setAppointmentsPage)
      .catch((error: Error) => setDashboardError(error.message))
      .finally(() => setDashboardLoading(false))
  }, [route, appointmentsSearchDebounced, appointmentsWindow, appointmentsPage.limit, appointmentsPage.offset])

  useEffect(() => {
    if (route !== 'scheduleChanges') {
      return
    }

    const query = toQuery({
      q: scheduleChangesSearchDebounced,
      limit: scheduleChangesPage.limit,
      offset: scheduleChangesPage.offset,
    })

    setDashboardLoading(true)
    setDashboardError(null)
    void api<ScheduleChangeListResponse>(`/api/dashboard/schedule-changes${query}`)
      .then(setScheduleChangesPage)
      .catch((error: Error) => setDashboardError(error.message))
      .finally(() => setDashboardLoading(false))
  }, [route, scheduleChangesSearchDebounced, scheduleChangesPage.limit, scheduleChangesPage.offset])

  const navigate = (href: string) => {
    if (window.location.pathname === href) {
      return
    }
    window.history.pushState({}, '', href)
    setRouteState(parseRoute(href))
  }

  const refreshChatDetail = async (sessionId: number): Promise<void> => {
    try {
      const detail = await api<DashboardSessionDetail>(`/api/dashboard/sessions/${sessionId}`)
      setChatSummary(detail.session)
      setChatActions(detail.actions.slice(-12).reverse())
    } catch {
      // Session details are secondary to chat flow, so failures here stay non-blocking.
    }
  }

  const ensureChatSession = async (): Promise<number> => {
    if (chatSessionId) {
      return chatSessionId
    }
    const created = await api<{ session_id: number }>('/agent/sessions', {
      method: 'POST',
      body: JSON.stringify({ channel: 'web' }),
    })
    setChatSessionId(created.session_id)
    return created.session_id
  }

  const speakText = (text: string) => {
    window.speechSynthesis.cancel()
    const utterance = new SpeechSynthesisUtterance(text)
    utterance.rate = 1.03
    utterance.pitch = 1
    utterance.onstart = () => setRecognitionState('speaking')
    utterance.onend = () => setRecognitionState('idle')
    utterance.onerror = () => setRecognitionState('idle')
    window.speechSynthesis.speak(utterance)
  }

  const sendMessage = async (rawMessage: string, fromVoice = false) => {
    const message = rawMessage.trim()
    if (!message || loadingChat) {
      return
    }
    setChatError(null)
    setLoadingChat(true)
    setMessages((prev) => [...prev, { role: 'user', content: message }])
    setComposer('')

    if (fromVoice) {
      setRecognitionState('thinking')
    }

    try {
      const sessionId = await ensureChatSession()
      const response = await api<ChatResponse>('/agent/chat', {
        method: 'POST',
        body: JSON.stringify({ message, session_id: sessionId, debug: true }),
      })
      setChatSessionId(response.session_id)
      setMessages((prev) => [...prev, { role: 'assistant', content: response.response }])
      await refreshChatDetail(response.session_id)

      if (voiceEnabled && fromVoice) {
        speakText(response.response)
      } else {
        setRecognitionState('idle')
      }
    } catch (error) {
      setChatError(error instanceof Error ? error.message : 'Unknown chat error')
      setRecognitionState('idle')
    } finally {
      setLoadingChat(false)
    }
  }

  const beginListening = () => {
    if (!speechSupported || loadingChat) {
      return
    }
    const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!Ctor) {
      return
    }

    window.speechSynthesis.cancel()
    recognitionRef.current?.stop()
    const recognition = new Ctor()
    recognition.continuous = false
    recognition.interimResults = false
    recognition.lang = 'en-US'
    recognition.onresult = (event) => {
      const transcript = String(event.results?.[0]?.[0]?.transcript ?? '')
      if (transcript) {
        void sendMessage(transcript, true)
      }
    }
    recognition.onerror = () => {
      setChatError('Voice recognition failed. Please try again.')
      setRecognitionState('idle')
    }
    recognition.onend = () => {
      if (recognitionState === 'listening') {
        setRecognitionState('idle')
      }
    }
    recognitionRef.current = recognition
    setRecognitionState('listening')
    recognition.start()
  }

  const stopVoiceOutput = () => {
    recognitionRef.current?.stop()
    window.speechSynthesis.cancel()
    setRecognitionState('idle')
  }

  const onComposerSubmit = (event: FormEvent) => {
    event.preventDefault()
    void sendMessage(composer)
  }

  const updateAppointmentsOffset = (offset: number) => {
    setAppointmentsPage((prev) => ({ ...prev, offset: Math.max(0, offset) }))
  }

  const updateScheduleChangesOffset = (offset: number) => {
    setScheduleChangesPage((prev) => ({ ...prev, offset: Math.max(0, offset) }))
  }

  return (
    <div className="app-shell">
      <header className="top-bar">
        <div>
          <p className="eyebrow">Gulf Breeze Air</p>
          <h1>Live Service AI Console</h1>
        </div>
        <nav className="nav-pills" aria-label="Primary">
          {NAV_ITEMS.map((item) => (
            <button
              type="button"
              key={item.route}
              className={route === item.route ? 'active' : ''}
              onClick={() => navigate(item.href)}
            >
              {item.label}
            </button>
          ))}
        </nav>
      </header>

      {publicConfig?.public_phone_enabled && (
        <aside className="phone-banner">
          Public phone line for live demo: {publicConfig.public_phone_number}
        </aside>
      )}

      {route === 'chat' && (
        <main className="chat-layout">
          <section className="chat-panel panel">
            <h2>Customer-Facing Assistant</h2>
            <p className="muted">Same-origin session persisted in backend and mirrored in dashboard.</p>

            <div className="message-stream">
              {messages.length === 0 && (
                <div className="placeholder-card">
                  Try: "I need to reschedule my HVAC maintenance" or "Do I have any unpaid invoices?"
                </div>
              )}
              {messages.map((message, index) => (
                <article key={`${message.role}-${index}`} className={`bubble ${message.role}`}>
                  <p className="role">{message.role === 'assistant' ? 'Assistant' : 'Customer'}</p>
                  <p>{message.content}</p>
                </article>
              ))}
              <div ref={messagesEndRef} />
            </div>

            <form onSubmit={onComposerSubmit} className="composer">
              <textarea
                rows={3}
                value={composer}
                onChange={(event) => setComposer(event.target.value)}
                placeholder="Type your message..."
              />
              <div className="row">
                <button type="submit" disabled={loadingChat || !composer.trim()}>
                  {loadingChat ? 'Thinking...' : 'Send Message'}
                </button>
                <button
                  type="button"
                  className={voiceEnabled ? 'toggle active' : 'toggle'}
                  onClick={() => setVoiceEnabled((value) => !value)}
                  disabled={!speechSupported}
                >
                  Voice Mode {speechSupported ? (voiceEnabled ? 'On' : 'Off') : 'Unavailable'}
                </button>
                <button type="button" onClick={beginListening} disabled={!voiceEnabled || loadingChat}>
                  {recognitionState === 'listening' ? 'Listening...' : 'Speak'}
                </button>
                <button type="button" className="ghost" onClick={stopVoiceOutput}>
                  Stop Voice
                </button>
              </div>
            </form>
            {chatError && <p className="error-text">{chatError}</p>}
          </section>

          <section className="side-panel panel">
            <h3>Session Visibility</h3>
            <dl className="stats-list">
              <div>
                <dt>Session ID</dt>
                <dd>{chatSessionId ?? '-'}</dd>
              </div>
              <div>
                <dt>Status</dt>
                <dd>{chatSummary?.status ?? '-'}</dd>
              </div>
              <div>
                <dt>Customer</dt>
                <dd>{chatSummary?.customer_name ?? '-'}</dd>
              </div>
              <div>
                <dt>Messages</dt>
                <dd>{chatSummary?.message_count ?? 0}</dd>
              </div>
              <div>
                <dt>Actions</dt>
                <dd>{chatSummary?.action_count ?? 0}</dd>
              </div>
            </dl>

            <h3>Agent Tool Activity</h3>
            <ul className="activity-list">
              {chatActions.length === 0 && <li>No tool activity yet.</li>}
              {chatActions.map((action) => (
                <li key={action.id}>
                  <strong>{action.display_label}</strong>
                  {action.detail && <span>{action.detail}</span>}
                  <span>{formatDateTime(action.timestamp)}</span>
                  <small>
                    {action.status ?? 'unknown'} {action.latency_ms ? `• ${action.latency_ms} ms` : ''}
                  </small>
                </li>
              ))}
            </ul>
          </section>
        </main>
      )}

      {route === 'dashboard' && (
        <main className="panel">
          <h2>Office Operations Dashboard</h2>
          {dashboardLoading && <p>Loading summary...</p>}
          {dashboardError && <p className="error-text">{dashboardError}</p>}
          {dashboardSummary && (
            <>
              <div className="kpi-grid">
                <article><h3>Customers</h3><p>{dashboardSummary.customers}</p></article>
                <article><h3>Jobs</h3><p>{dashboardSummary.jobs}</p></article>
                <article><h3>AI Conversations</h3><p>{dashboardSummary.ai_conversations}</p></article>
                <article><h3>Active Sessions</h3><p>{dashboardSummary.active_sessions}</p></article>
                <article><h3>Human Handoffs</h3><p>{dashboardSummary.human_handoffs}</p></article>
                <article><h3>Open Invoices</h3><p>{dashboardSummary.open_invoices}</p></article>
                <article><h3>Upcoming Appointments</h3><p>{dashboardSummary.upcoming_appointments}</p></article>
                <article><h3>Outstanding Balance</h3><p>${dashboardSummary.outstanding_balance.toLocaleString()}</p></article>
              </div>

              <div className="split-grid">
                <section>
                  <div className="section-header">
                    <h3>Upcoming Appointments</h3>
                    <button type="button" className="linkish" onClick={() => navigate('/dashboard/appointments')}>
                      View all
                    </button>
                  </div>
                  <input
                    className="search-input"
                    type="search"
                    value={dashboardSearch}
                    onChange={(event) => setDashboardSearch(event.target.value)}
                    placeholder="Search by customer, job, address, tech"
                  />
                  <ul className="simple-list">
                    {dashboardPreviewAppointments.map((item) => (
                      <li key={item.job_id}>
                        <strong>{item.customer_name ?? 'Unknown customer'}</strong>
                        <span>{item.address ?? 'Address unavailable'}</span>
                        <span>{item.description ?? 'No description'}</span>
                        <small>{formatWindow(item.effective_start_at, item.effective_end_at)}</small>
                        {item.technicians.length > 0 && <small>Tech: {item.technicians.join(', ')}</small>}
                        <div className="weather-chip">
                          {item.weather ? (
                            <>
                              <strong>{item.weather.condition}</strong>
                              <span>{item.weather.temperature_f}F</span>
                              <span>Rain {item.weather.precipitation_probability}%</span>
                              <span>Wind {item.weather.wind_mph} mph</span>
                              {item.weather.risk_indicator && <span>{item.weather.risk_indicator}</span>}
                            </>
                          ) : (
                            <span>Forecast unavailable</span>
                          )}
                        </div>
                      </li>
                    ))}
                    {dashboardPreviewAppointments.length === 0 && (
                      <li>
                        <strong>No appointments found</strong>
                        <span>Try a different search term.</span>
                      </li>
                    )}
                  </ul>
                </section>

                <section>
                  <h3>Outstanding Accounts</h3>
                  <ul className="simple-list">
                    {dashboardSummary.outstanding_accounts.slice(0, 8).map((item) => (
                      <li key={item.invoice_id}>
                        <strong>{item.customer_name ?? 'Unknown customer'}</strong>
                        <span>{item.status ?? 'open'}</span>
                        <small>${item.due_amount.toLocaleString()}</small>
                      </li>
                    ))}
                  </ul>
                </section>
              </div>

              <section className="panel section-panel">
                <div className="section-header">
                  <h3>Recent Schedule Changes</h3>
                  <button type="button" className="linkish" onClick={() => navigate('/dashboard/schedule-changes')}>
                    View history
                  </button>
                </div>
                <ul className="simple-list">
                  {dashboardSummary.recent_schedule_changes.map((change) => (
                    <li key={change.change_id}>
                      <strong>{change.customer_name ?? 'Unknown customer'} • Job {change.job_id}</strong>
                      <span>{formatWindow(change.original_start_at, change.original_end_at)} {'->'} {formatWindow(change.new_start_at, change.new_end_at)}</span>
                      <small>{change.changed_by} • {formatDateTime(change.created_at)}</small>
                    </li>
                  ))}
                  {dashboardSummary.recent_schedule_changes.length === 0 && (
                    <li>
                      <strong>No schedule changes yet</strong>
                      <span>Reschedules initiated by the assistant will appear here.</span>
                    </li>
                  )}
                </ul>
              </section>
            </>
          )}
        </main>
      )}

      {route === 'appointments' && (
        <main className="panel">
          <h2>Upcoming Appointments</h2>
          <p className="muted">Search and filter by upcoming window. Times are shown in Miami local time.</p>
          <div className="filters-row">
            <input
              className="search-input"
              type="search"
              value={appointmentsSearch}
              onChange={(event) => {
                setAppointmentsSearch(event.target.value)
                updateAppointmentsOffset(0)
              }}
              placeholder="Search by customer, job, address, technician"
            />
            <div className="pill-switches" role="group" aria-label="Time window">
              {(['24h', '7d', '30d'] as AppointmentWindow[]).map((windowKey) => (
                <button
                  key={windowKey}
                  type="button"
                  className={appointmentsWindow === windowKey ? 'active' : ''}
                  onClick={() => {
                    setAppointmentsWindow(windowKey)
                    updateAppointmentsOffset(0)
                  }}
                >
                  Next {windowKey}
                </button>
              ))}
            </div>
          </div>

          {dashboardLoading && <p>Loading appointments...</p>}
          {dashboardError && <p className="error-text">{dashboardError}</p>}

          <ul className="simple-list">
            {appointmentsPage.items.map((item) => (
              <li key={item.job_id}>
                <strong>{item.customer_name ?? 'Unknown customer'} • Job {item.job_id}</strong>
                <span>{item.address ?? 'Address unavailable'}</span>
                <span>{item.description ?? 'No description'}</span>
                <small>{formatWindow(item.effective_start_at, item.effective_end_at)}</small>
                {item.has_schedule_override && (
                  <small>
                    Updated from {formatWindow(item.original_start_at ?? null, item.original_end_at ?? null)}
                  </small>
                )}
                {item.technicians.length > 0 && <small>Tech: {item.technicians.join(', ')}</small>}
                <div className="weather-chip">
                  {item.weather ? (
                    <>
                      <strong>{item.weather.condition}</strong>
                      <span>{item.weather.temperature_f}F</span>
                      <span>Rain {item.weather.precipitation_probability}%</span>
                      <span>Wind {item.weather.wind_mph} mph</span>
                    </>
                  ) : (
                    <span>Forecast unavailable</span>
                  )}
                </div>
              </li>
            ))}
            {appointmentsPage.items.length === 0 && (
              <li>
                <strong>No appointments found</strong>
                <span>Adjust the window or search terms.</span>
              </li>
            )}
          </ul>

          <div className="pagination-row">
            <button
              type="button"
              onClick={() => updateAppointmentsOffset(appointmentsPage.offset - appointmentsPage.limit)}
              disabled={appointmentsPage.offset <= 0}
            >
              Previous
            </button>
            <span>
              Showing {appointmentsPage.total === 0 ? 0 : appointmentsPage.offset + 1}-
              {Math.min(appointmentsPage.offset + appointmentsPage.limit, appointmentsPage.total)} of {appointmentsPage.total}
            </span>
            <button
              type="button"
              onClick={() => updateAppointmentsOffset(appointmentsPage.offset + appointmentsPage.limit)}
              disabled={appointmentsPage.offset + appointmentsPage.limit >= appointmentsPage.total}
            >
              Next
            </button>
          </div>
        </main>
      )}

      {route === 'scheduleChanges' && (
        <main className="panel">
          <h2>Schedule Changes</h2>
          <p className="muted">Reschedule history generated by assistant conversations across channels.</p>
          <div className="filters-row">
            <input
              className="search-input"
              type="search"
              value={scheduleChangesSearch}
              onChange={(event) => {
                setScheduleChangesSearch(event.target.value)
                updateScheduleChangesOffset(0)
              }}
              placeholder="Search by customer, reason, address, job, session"
            />
          </div>

          {dashboardLoading && <p>Loading schedule changes...</p>}
          {dashboardError && <p className="error-text">{dashboardError}</p>}

          <ul className="simple-list">
            {scheduleChangesPage.items.map((change) => (
              <li key={change.change_id}>
                <strong>{change.customer_name ?? 'Unknown customer'} • Job {change.job_id}</strong>
                <span>{change.address ?? 'Address unavailable'}</span>
                <small>{formatWindow(change.original_start_at, change.original_end_at)} {'->'} {formatWindow(change.new_start_at, change.new_end_at)}</small>
                {change.reason && <span>Reason: {change.reason}</span>}
                <div className="badges-row">
                  <span className={`status-badge ${change.status.toLowerCase()}`}>{change.status}</span>
                  {change.channel && <span className="channel-badge">{change.channel}</span>}
                  {change.agent_session_id && <span className="channel-badge">Session #{change.agent_session_id}</span>}
                </div>
                <small>{change.changed_by} • {formatDateTime(change.created_at)}</small>
              </li>
            ))}
            {scheduleChangesPage.items.length === 0 && (
              <li>
                <strong>No schedule changes found</strong>
                <span>Try a different search term.</span>
              </li>
            )}
          </ul>

          <div className="pagination-row">
            <button
              type="button"
              onClick={() => updateScheduleChangesOffset(scheduleChangesPage.offset - scheduleChangesPage.limit)}
              disabled={scheduleChangesPage.offset <= 0}
            >
              Previous
            </button>
            <span>
              Showing {scheduleChangesPage.total === 0 ? 0 : scheduleChangesPage.offset + 1}-
              {Math.min(scheduleChangesPage.offset + scheduleChangesPage.limit, scheduleChangesPage.total)} of {scheduleChangesPage.total}
            </span>
            <button
              type="button"
              onClick={() => updateScheduleChangesOffset(scheduleChangesPage.offset + scheduleChangesPage.limit)}
              disabled={scheduleChangesPage.offset + scheduleChangesPage.limit >= scheduleChangesPage.total}
            >
              Next
            </button>
          </div>
        </main>
      )}

      {route === 'sessions' && (
        <main className="panel">
          <h2>Conversation Sessions</h2>
          {dashboardLoading && <p>Loading sessions...</p>}
          {dashboardError && <p className="error-text">{dashboardError}</p>}
          <ul className="session-list">
            {dashboardSessions.map((session) => (
              <li key={session.session_id}>
                <button type="button" onClick={() => navigate(`/dashboard/sessions/${session.session_id}`)}>
                  <strong>Session #{session.session_id}</strong>
                  <span>{session.customer_name ?? 'Unknown customer'}</span>
                  <span>{session.channel} • {session.status}</span>
                  <small>{formatDateTime(session.started_at)}</small>
                </button>
              </li>
            ))}
          </ul>
        </main>
      )}

      {route === 'handoffs' && (
        <main className="panel">
          <h2>Human Handoffs</h2>
          {dashboardLoading && <p>Loading handoffs...</p>}
          {dashboardError && <p className="error-text">{dashboardError}</p>}
          <ul className="session-list">
            {dashboardHandoffs.map((session) => (
              <li key={session.session_id}>
                <button type="button" onClick={() => navigate(`/dashboard/sessions/${session.session_id}`)}>
                  <strong>Session #{session.session_id}</strong>
                  <span>{session.customer_name ?? 'Unknown customer'}</span>
                  <span>{session.handoff_reason ?? 'Handoff requested'}</span>
                  <small>{formatDateTime(session.started_at)}</small>
                </button>
              </li>
            ))}
          </ul>
        </main>
      )}

      {route === 'sessionDetail' && (
        <main className="panel">
          <h2>Session Detail #{selectedSessionId}</h2>
          {dashboardLoading && <p>Loading detail...</p>}
          {dashboardError && <p className="error-text">{dashboardError}</p>}
          {sessionDetail && (
            <div className="split-grid">
              <section>
                <h3>Conversation</h3>
                <ul className="simple-list">
                  {sessionDetail.conversation.map((message, index) => (
                    <li key={`${message.role}-${index}`}>
                      <strong>{message.role}</strong>
                      <span>{message.content}</span>
                    </li>
                  ))}
                </ul>
              </section>
              <section>
                <h3>Actions</h3>
                <ul className="simple-list">
                  {sessionDetail.actions.map((action) => (
                    <li key={action.id}>
                      <strong>{action.display_label}</strong>
                      {action.detail && <span>{action.detail}</span>}
                      <span>{action.status ?? 'unknown'}</span>
                      <small>{formatDateTime(action.timestamp)}</small>
                    </li>
                  ))}
                </ul>
              </section>
            </div>
          )}
        </main>
      )}
    </div>
  )
}

export default App
