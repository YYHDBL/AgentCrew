import { useRef, useState } from 'react'
import type { ComponentProps, ReactNode } from 'react'
import { Button, Tooltip } from 'antd'
import { CheckOutlined, CopyOutlined, LinkOutlined } from '@ant-design/icons'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export function CopyButton({ text, label = '复制回复' }: { text: string; label?: string }): JSX.Element {
  const [copied, setCopied] = useState(false)
  const copy = async (): Promise<void> => {
    await navigator.clipboard.writeText(text)
    setCopied(true)
  }
  return <><Tooltip title={copied ? '已复制' : label}><Button type="text" size="small" aria-label={copied ? '已复制' : label} icon={copied ? <CheckOutlined /> : <CopyOutlined />} onClick={() => void copy()} /></Tooltip><span className="sr-only" role="status">{copied ? '已复制到剪贴板' : ''}</span></>
}

function CodeBlock({ children }: { children: ReactNode }): JSX.Element {
  const code = useRef<HTMLPreElement>(null)
  const [copied, setCopied] = useState(false)
  const copy = async (): Promise<void> => {
    if (code.current === null) throw new Error('代码块尚未显示')
    await navigator.clipboard.writeText(code.current.textContent ?? '')
    setCopied(true)
  }
  return <div className="code-block"><div className="code-toolbar"><span>代码</span><Button type="text" size="small" aria-label={copied ? '代码已复制' : '复制代码'} icon={copied ? <CheckOutlined /> : <CopyOutlined />} onClick={() => void copy()}>{copied ? '已复制' : '复制代码'}</Button></div><pre ref={code}>{children}</pre></div>
}

function MarkdownLink({ children, href }: ComponentProps<'a'>): JSX.Element {
  const [copied, setCopied] = useState(false)
  if (!href) return <span>{children}</span>
  return <Tooltip title={copied ? '链接已复制，可在浏览器中打开' : `复制链接：${href}`}><button className="markdown-link" type="button" aria-label={`复制链接：${href}`} onClick={() => { void navigator.clipboard.writeText(href).then(() => setCopied(true)) }}>{children}<LinkOutlined aria-hidden="true" /></button></Tooltip>
}

export function MessageContent({ text }: { text: string }): JSX.Element {
  return <div className="markdown-body"><Markdown remarkPlugins={[remarkGfm]} skipHtml components={{
    pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
    table: ({ children }) => <div className="markdown-table" tabIndex={0} aria-label="回复表格，可横向滚动"><table>{children}</table></div>,
    a: MarkdownLink,
    img: ({ alt, src }) => <span className="markdown-image">{alt || '图片'}{src && <CopyButton text={src} label="复制图片链接" />}</span>
  }}>{text}</Markdown></div>
}
