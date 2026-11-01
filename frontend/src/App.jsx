import { useEffect, useRef, useState } from 'react';
import { Activity, ArrowRight, Check, Clock3, RotateCcw, ScanSearch, Shield, ShieldAlert, ShieldCheck, Upload, X, Sun, Moon } from 'lucide-react';

const MAX_LENGTH = 10000;
const ACCEPT = '.txt,.md,.pdf,.docx,.png,.jpg,.jpeg,.pptx,.xlsx,.json,.xml,.html,.htm';
const FORMATS = 'TXT, MD, PDF, DOCX, PNG, JPG, JPEG, PPTX, XLSX, JSON, XML, HTML, HTM';
const AUDIO_ACCEPT = '.wav,.mp3,.m4a,.flac';

function getInitialTheme() {
  const rootTheme = document.documentElement.dataset.theme;
  if (rootTheme === 'light' || rootTheme === 'dark') return rootTheme;
  try {
    const savedTheme = localStorage.getItem('jailbreakguard-theme');
    if (savedTheme === 'light' || savedTheme === 'dark') return savedTheme;
  } catch { /* Storage may be unavailable in private browsing. */ }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function isScanResult(value) {
  return value && typeof value.status === 'string'
    && (value.level === 'clear' || value.level === 'warning')
    && typeof value.explanation === 'string' && Array.isArray(value.evidence)
    && value.evidence.every((item) => item && typeof item.rule === 'string' && typeof item.evidence === 'string');
}

function ResultsPanel({ result, loading }) {
  return <section className="card results-card" aria-live="polite" aria-busy={loading}>
    <div className="card-heading"><div><span className="eyebrow">ASSESSMENT</span><h2>Scan results</h2></div><span className="step-number">02</span></div>
    {!result && <div className="empty-state"><span className="empty-shield"><ShieldCheck size={25} /></span><strong>Ready when you are</strong><p>Your assessment and matched indicators will appear here.</p></div>}
    {result && <div className="result-content">
      {result.filename && <p className="file-result-meta"><strong>{result.filename}</strong><span>{String(result.format).toUpperCase()}</span>{result.transcription_status ? <span>Transcription: {result.transcription_status}</span> : <span>Extraction: {result.extraction_status}</span>}<span>{(result.transcript_character_count ?? result.character_count ?? 0).toLocaleString()} characters</span></p>}
      <div className={`result-banner ${result.level}`}><span className="result-icon">{result.level === 'warning' ? <ShieldAlert size={20} /> : <ShieldCheck size={20} />}</span><div><span className="eyebrow">RISK ASSESSMENT</span><h3>{result.status}</h3></div><span className="risk-chip">{result.level === 'warning' ? 'REVIEW' : 'CLEAR'}</span></div>
      <p className="result-summary">{result.explanation}</p>
      <div className="evidence-panel"><div className="evidence-heading"><strong>Matched indicators</strong><span>{result.evidence.length}</span></div>{result.evidence.length ? <ul>{result.evidence.map((match) => <li key={match.rule}><span className="evidence-mark"><Check size={14} /></span><span>{match.category ? `${match.category}: ` : ''}{match.evidence}</span></li>)}</ul> : <p className="no-evidence">No rule matched the extracted text.</p>}</div>
      <p className="disclaimer"><Shield size={14} /> Rule based indicators are not proof that a prompt is malicious.</p>
    </div>}
  </section>;
}

export default function App() {
  const [theme, setTheme] = useState(getInitialTheme);
  const [prompt, setPrompt] = useState('');
  const [result, setResult] = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [fileLoading, setFileLoading] = useState(false);
  const [file, setFile] = useState(null);
  const [audioFile, setAudioFile] = useState(null);
  const [audioLoading, setAudioLoading] = useState(false);
  const [error, setError] = useState('');
  const fileInput = useRef(null);
  const audioInput = useRef(null);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#0b1020' : '#f5f7fb');
    try { localStorage.setItem('jailbreakguard-theme', theme); } catch { /* Theme still works for this session. */ }
  }, [theme]);

  function addHistory(payload) { setHistory((items) => [{ id: Date.now(), level: payload.level, status: payload.status, time: new Date() }, ...items]); }

  async function scanPrompt(event) {
    event.preventDefault();
    if (loading) return;
    if (!prompt.trim()) { setError('Enter a prompt before scanning.'); return; }
    setError(''); setLoading(true);
    try {
      const response = await fetch('/scan', { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify({ text: prompt }) });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `The scan failed with HTTP ${response.status}.`);
      if (!isScanResult(payload)) throw new Error('Scanner returned an unexpected result format.');
      setResult(payload); addHistory(payload);
    } catch (err) { setError(err instanceof TypeError ? 'Could not reach the scanner. Check that Flask is running.' : err.message || 'The scan could not be completed.'); }
    finally { setLoading(false); }
  }

  async function scanFile() {
    if (!file || fileLoading) return;
    setError(''); setFileLoading(true);
    try {
      const body = new FormData(); body.append('file', file);
      const response = await fetch('/scan-file', { method: 'POST', body });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'The file could not be scanned.');
      if (!isScanResult(payload)) throw new Error('Scanner returned an unexpected result format.');
      setResult(payload); addHistory(payload);
    } catch (err) { setError(err instanceof TypeError ? 'Could not reach the scanner. Check that Flask is running.' : err.message || 'The file could not be scanned.'); }
    finally { setFileLoading(false); }
  }

  async function scanAudio() {
    if (!audioFile || audioLoading) return;
    setError(''); setAudioLoading(true);
    try {
      const body = new FormData(); body.append('file', audioFile);
      const response = await fetch('/scan-audio', { method: 'POST', body });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'The audio could not be transcribed and scanned.');
      if (!isScanResult(payload) || typeof payload.transcription_status !== 'string') throw new Error('Scanner returned an unexpected audio result format.');
      setResult(payload); addHistory(payload);
    } catch (err) { setError(err instanceof TypeError ? 'Could not reach the scanner. Check that Flask is running.' : err.message || 'The audio could not be scanned.'); }
    finally { setAudioLoading(false); }
  }

  function chooseFile(nextFiles) {
    const picked = nextFiles?.[0];
    if (picked) { setFile(picked); setError(''); }
  }

  function chooseAudio(nextFiles) {
    const picked = nextFiles?.[0];
    if (picked) { setAudioFile(picked); setError(''); }
  }

  function removeFile() {
    setFile(null);
    if (fileInput.current) fileInput.current.value = '';
  }

  function resetScan() { setPrompt(''); setResult(null); setError(''); document.getElementById('prompt-text')?.focus(); }

  return <div className="app-shell" id="top">
    <aside className="sidebar"><a className="brand" href="#top"><span className="brand-icon"><Shield size={20} /></span><span><strong>JailbreakGuard</strong><small>Prompt security</small></span></a><p className="nav-label">WORKSPACE</p><nav aria-label="Workspace navigation"><a className="nav-link active" href="#scanner"><ScanSearch size={17} />Prompt scanner</a><a className="nav-link" href="#history"><Clock3 size={17} />Session history</a></nav><div className="sidebar-bottom"><span className="local-dot" />Local analysis<p>Your prompts stay in this session and are not saved.</p><span className="module-tag">MODULE 05 · AUDIO SCAN</span></div></aside>
    <main className="main-area"><header className="topbar"><div className="breadcrumbs">Security tools <span>/</span> <strong>Prompt scanner</strong></div><div className="topbar-actions"><div className="connection"><span className="local-dot" />LOCAL WORKSPACE</div><div className="theme-toggle" role="group" aria-label="Color theme"><button type="button" className={theme === 'light' ? 'selected' : ''} aria-label="Use light theme" aria-pressed={theme === 'light'} onClick={() => setTheme('light')}><Sun size={15} /><span>Light</span></button><button type="button" className={theme === 'dark' ? 'selected' : ''} aria-label="Use dark theme" aria-pressed={theme === 'dark'} onClick={() => setTheme('dark')}><Moon size={15} /><span>Dark</span></button></div></div></header>
      <div className="content-wrap"><section className="page-intro"><div><span className="eyebrow intro-eyebrow"><Activity size={14} /> AI SECURITY <span className="intro-divider">/</span> MODULE 05</span><h1>Prompt security<br /><span>workspace.</span></h1><p>Review text, files, and local audio transcripts for common jailbreak and prompt injection indicators.</p></div><div className="intro-badge"><Shield size={31} /><span>LOCAL BY DESIGN</span></div></section>
        <div className="dashboard-grid"><div className="main-column">
          <section className="card scan-card" id="scanner"><div className="card-heading"><div><span className="eyebrow">INPUT</span><h2>Prompt to inspect</h2></div><span className="step-number">01</span></div><form onSubmit={scanPrompt} noValidate><label htmlFor="prompt-text">Paste or type the text you want to review</label><textarea id="prompt-text" maxLength={MAX_LENGTH} value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="Enter a prompt for a first-pass security review…" /><div className="input-footer"><span>{prompt.length.toLocaleString()} / {MAX_LENGTH.toLocaleString()} characters</span><div className="form-actions"><button type="button" className="button-quiet" onClick={resetScan} disabled={loading}><RotateCcw size={15} />Clear</button><button type="submit" className="button-primary" disabled={loading}>{loading ? <><span className="spinner" />Scanning…</> : <>Scan prompt <ArrowRight size={16} /></>}</button></div></div></form></section>

          <section className="card scan-card file-scan-card"><div className="card-heading"><div><span className="eyebrow">FILE SCAN</span><h2>Upload a document or image</h2></div></div>
            <input ref={fileInput} id="file-upload" className="file-input" type="file" accept={ACCEPT} aria-label="Choose a file to scan" tabIndex={-1} onChange={(event) => chooseFile(event.target.files)} />
            <div className="upload-drop" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); chooseFile(event.dataTransfer.files); }}>
              <span className="upload-icon"><Upload size={23} aria-hidden="true" /></span>
              <h3>Drop your file here</h3>
              <p className="upload-instructions">or select a file from your device</p>
              <button type="button" className="button-primary browse-button" onClick={() => fileInput.current?.click()}>Browse files</button>
              <p className="upload-formats">Supported formats: {FORMATS} <span aria-hidden="true">·</span> Maximum size: 10 MB</p>
            </div>
            {file && <div className="selected-file"><span className="selected-file-name" title={file.name}>{file.name}</span><span className="selected-file-size">{(file.size / 1024).toFixed(1)} KB</span><button type="button" className="button-quiet remove-file" aria-label="Remove selected file" onClick={removeFile}><X size={15} /></button></div>}
            <div className="input-footer"><span>File contents are processed locally and not saved.</span><button type="button" className="button-primary" onClick={scanFile} disabled={!file || fileLoading}>{fileLoading ? <><span className="spinner" />Scanning…</> : <>Scan file <ArrowRight size={16} /></>}</button></div>
          </section>
          <section className="card scan-card file-scan-card audio-scan-card"><div className="card-heading"><div><span className="eyebrow">AUDIO SCAN</span><h2>Transcribe and scan audio</h2></div></div>
            <input ref={audioInput} id="audio-upload" className="file-input" type="file" accept={AUDIO_ACCEPT} aria-label="Choose an audio file to transcribe and scan" tabIndex={-1} onChange={(event) => chooseAudio(event.target.files)} />
            <div className="upload-drop audio-drop" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); chooseAudio(event.dataTransfer.files); }}>
              <span className="upload-icon"><Upload size={23} aria-hidden="true" /></span><h3>Drop your audio here</h3><p className="upload-instructions">WAV, MP3, M4A, or FLAC · maximum 10 MB · up to 5 minutes</p>
              <button type="button" className="button-primary browse-button" onClick={() => audioInput.current?.click()}>Browse audio</button>
              <p className="audio-model-note">Local Whisper transcription. Model weights may need to download on first scan.</p>
            </div>
            {audioFile && <div className="selected-file"><span className="selected-file-name" title={audioFile.name}>{audioFile.name}</span><span className="selected-file-size">{(audioFile.size / 1024).toFixed(1)} KB</span><button type="button" className="button-quiet remove-file" aria-label="Remove selected audio" onClick={() => { setAudioFile(null); if (audioInput.current) audioInput.current.value = ''; }}><X size={15} /></button></div>}
            <div className="input-footer"><span>Audio and transcript remain local to this request.</span><button type="button" className="button-primary" onClick={scanAudio} disabled={!audioFile || audioLoading}>{audioLoading ? <><span className="spinner" />Transcribing…</> : <>Transcribe and scan <ArrowRight size={16} /></>}</button></div>
          </section>
          <ResultsPanel result={result} loading={loading || fileLoading || audioLoading} />
        </div>
        <aside className="card history-card" id="history"><div className="card-heading"><div><span className="eyebrow">THIS SESSION</span><h2>Scan history</h2></div><span className="history-count">{history.length}</span></div><p className="history-caption">Previous results for this session. Prompt text is never kept here.</p>{history.length ? <ol className="history-list">{history.map((item) => <li key={item.id}><span className={`history-status ${item.level}`}>{item.level === 'warning' ? <ShieldAlert size={16} /> : <ShieldCheck size={16} />}</span><span className="history-label"><strong>{item.level === 'warning' ? 'Indicators found' : 'No indicators found'}</strong><small>{item.status}</small></span><time>{item.time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time></li>)}</ol> : <div className="history-empty"><span><Clock3 size={19} /></span><strong>No scans yet</strong><p>Completed assessments will appear here.</p></div>}<div className="history-footnote"><Shield size={14} /> History clears when you leave this page.</div></aside></div>
        {error && <p className="form-error" role="alert">{error}</p>}<footer className="page-footer"><span>JailbreakGuard · Module 5</span><span>Rule based indicators support review; they do not establish intent.</span></footer>
      </div>
    </main>
  </div>;
}
