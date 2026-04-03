import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Components } from 'react-markdown'

const components: Components = {
  h1: ({ children }) => <h1 className="text-lg font-bold text-gray-100 mt-4 mb-2 border-b border-gray-700 pb-1">{children}</h1>,
  h2: ({ children }) => <h2 className="text-base font-semibold text-gray-200 mt-3 mb-1.5">{children}</h2>,
  h3: ({ children }) => <h3 className="text-sm font-semibold text-gray-300 mt-2 mb-1">{children}</h3>,
  p:  ({ children }) => <p  className="text-sm text-gray-300 leading-relaxed my-1.5">{children}</p>,
  a:  ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer" className="text-indigo-400 hover:text-indigo-300 underline underline-offset-2">
      {children}
    </a>
  ),
  ul: ({ children }) => <ul className="list-disc list-inside space-y-0.5 my-1.5 text-sm text-gray-300">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal list-inside space-y-0.5 my-1.5 text-sm text-gray-300">{children}</ol>,
  li: ({ children }) => <li className="text-gray-300">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-gray-100">{children}</strong>,
  em:     ({ children }) => <em     className="italic text-gray-400">{children}</em>,
  code: ({ className, children, ...props }) => {
    const isBlock = className?.startsWith('language-')
    if (isBlock) {
      return (
        <code className="block bg-gray-950 rounded-lg px-3 py-2 text-xs font-mono text-green-300 overflow-x-auto whitespace-pre my-2">
          {children}
        </code>
      )
    }
    return (
      <code className="bg-gray-800 rounded px-1 py-0.5 text-xs font-mono text-indigo-300" {...props}>
        {children}
      </code>
    )
  },
  pre: ({ children }) => <pre className="my-2 overflow-x-auto">{children}</pre>,
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-indigo-500 pl-3 my-2 text-sm text-gray-400 italic">
      {children}
    </blockquote>
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto my-2">
      <table className="w-full text-xs border-collapse">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-gray-800">{children}</thead>,
  tbody: ({ children }) => <tbody className="divide-y divide-gray-700/50">{children}</tbody>,
  tr:   ({ children }) => <tr className="hover:bg-gray-800/40 transition-colors">{children}</tr>,
  th:   ({ children }) => <th className="px-3 py-2 text-left text-gray-400 font-semibold uppercase tracking-wider">{children}</th>,
  td:   ({ children }) => <td className="px-3 py-2 text-gray-300">{children}</td>,
  hr:   () => <hr className="border-gray-700 my-3" />,
}

interface Props {
  children: string
  className?: string
}

export default function Markdown({ children, className }: Props) {
  return (
    <div className={className}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  )
}
