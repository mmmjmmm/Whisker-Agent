'use client'

import {useState, useRef, forwardRef, useImperativeHandle} from 'react'
import {cn, formatFileSize} from '@/lib/utils'
import {ScrollArea, ScrollBar} from '@/components/ui/scroll-area'
import {Item, ItemActions, ItemContent, ItemDescription, ItemMedia, ItemTitle} from '@/components/ui/item'
import {Avatar, AvatarGroupCount} from '@/components/ui/avatar'
import {ArrowUp, FileText, Paperclip, XCircle, Loader2, Pause} from 'lucide-react'
import {Button} from '@/components/ui/button'
import {fileApi} from '@/lib/api/file'
import {skillApi} from '@/lib/api/skill'
import type {AgentMode, FileInfo, SkillListItem} from '@/lib/api/types'
import {toast} from 'sonner'

type SkillMention = {start: number; query: string}

function getSkillMention(value: string, cursor: number): SkillMention | null {
  const prefix = value.slice(0, cursor)
  const match = prefix.match(/(?:^|\s)\$([^\s$]*)$/)
  if (!match) return null
  return {
    start: cursor - match[1].length - 1,
    query: match[1].toLowerCase(),
  }
}

interface ChatInputProps {
  className?: string
  onInputValueChange?: (value: string) => void
  onSend?: (message: string, files: FileInfo[]) => Promise<void>
  disabled?: boolean
  /** 当前会话 ID，上传附件时会关联到该会话 */
  sessionId?: string | null
  /** 任务是否正在运行中 */
  isRunning?: boolean
  /** 点击暂停按钮的回调 */
  onStop?: () => void
  /** 当前 Agent 执行模式 */
  mode: AgentMode
  /** 切换 Agent 执行模式 */
  onModeChange: (mode: AgentMode) => void
}

export interface ChatInputRef {
  setInputText: (text: string) => void
  getInputValue: () => string
  getFiles: () => FileInfo[]
}

export const ChatInput = forwardRef<ChatInputRef, ChatInputProps>(
  ({ className, onInputValueChange, onSend, disabled = false, sessionId, isRunning = false, onStop, mode, onModeChange }, ref) => {
    const [files, setFiles] = useState<FileInfo[]>([])
    const [uploading, setUploading] = useState(false)
    const [sending, setSending] = useState(false)
    const [inputValue, setInputValue] = useState('')
    const [skills, setSkills] = useState<SkillListItem[]>([])
    const [loadingSkills, setLoadingSkills] = useState(false)
    const [mention, setMention] = useState<SkillMention | null>(null)
    const [activeSkillIndex, setActiveSkillIndex] = useState(0)
    const fileInputRef = useRef<HTMLInputElement>(null)
    const textareaRef = useRef<HTMLTextAreaElement>(null)
    const teamLocked = isRunning && mode === 'team'

    const matchingSkills = mention
      ? skills.filter((skill) => {
          if (!skill.enabled) return false
          const query = mention.query
          return skill.name.toLowerCase().includes(query)
            || skill.description.toLowerCase().includes(query)
        })
      : []

    const loadSkills = async () => {
      if (loadingSkills) return
      setLoadingSkills(true)
      try {
        const data = await skillApi.list()
        setSkills(data?.skills ?? [])
      } catch (error) {
        setMention(null)
        toast.error(error instanceof Error ? error.message : '获取 Skill 列表失败')
      } finally {
        setLoadingSkills(false)
      }
    }

    const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      const value = e.target.value
      setInputValue(value)
      onInputValueChange?.(value)
      const nextMention = getSkillMention(
        value,
        e.target.selectionStart ?? value.length,
      )
      const isNewMention = nextMention
        && (!mention || nextMention.start !== mention.start)
      setMention(nextMention)
      setActiveSkillIndex(0)
      if (isNewMention) void loadSkills()
    }

    useImperativeHandle(ref, () => ({
      setInputText: (text: string) => {
        setInputValue(text)
        setMention(null)
        onInputValueChange?.(text)
        // 聚焦到输入框
        textareaRef.current?.focus()
      },
      getInputValue: () => inputValue,
      getFiles: () => files,
    }))

    const handleFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
      const selectedFiles = event.target.files
      if (!selectedFiles || selectedFiles.length === 0) {
        return
      }

      setUploading(true)

      try {
        const uploadPromises = Array.from(selectedFiles).map(async (file) => {
          try {
            const fileInfo = await fileApi.uploadFile({
              file,
              ...(sessionId && { session_id: sessionId }),
            })
            return fileInfo
          } catch (error) {
            const errorMessage = error instanceof Error ? error.message : '上传失败'
            toast.error(`文件「${file.name}」上传失败: ${errorMessage}`)
            return null
          }
        })

        const uploadedFiles = (await Promise.all(uploadPromises)).filter(
          (file): file is FileInfo => file !== null
        )

        if (uploadedFiles.length > 0) {
          setFiles((prev) => [...prev, ...uploadedFiles])
          toast.success(`成功上传 ${uploadedFiles.length} 个文件`)
        }
      } catch {
        toast.error('文件上传过程中发生错误')
      } finally {
        setUploading(false)
        // 重置input，以便可以重复选择同一文件
        if (fileInputRef.current) {
          fileInputRef.current.value = ''
        }
      }
    }

    const handleUploadClick = () => {
      fileInputRef.current?.click()
    }

    const handleRemoveFile = (fileId: string) => {
      setFiles((prev) => prev.filter((file) => file.id !== fileId))
    }

    const handleSend = async () => {
      const trimmedMessage = inputValue.trim()
      
      // 验证消息不为空
      if (!trimmedMessage) {
        toast.error('请输入消息内容')
        textareaRef.current?.focus()
        return
      }

      // 如果提供了 onSend 回调，使用它
      if (onSend) {
        setSending(true)
        try {
          await onSend(trimmedMessage, files)
          // 发送成功后清空输入框和文件列表
          setInputValue('')
          setMention(null)
          setFiles([])
          onInputValueChange?.('')
        } catch (error) {
          // 错误处理由 onSend 内部处理
          console.error('发送消息失败:', error)
        } finally {
          setSending(false)
        }
      }
    }

    const insertSkill = (skill: SkillListItem) => {
      if (!mention) return
      const cursor = textareaRef.current?.selectionStart ?? inputValue.length
      const inserted = `$${skill.name} `
      const next = inputValue.slice(0, mention.start)
        + inserted
        + inputValue.slice(cursor)
      const nextCursor = mention.start + inserted.length
      setInputValue(next)
      onInputValueChange?.(next)
      setMention(null)
      requestAnimationFrame(() => {
        textareaRef.current?.focus()
        textareaRef.current?.setSelectionRange(nextCursor, nextCursor)
      })
    }

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (mention) {
        if (e.key === 'ArrowDown' && matchingSkills.length > 0) {
          e.preventDefault()
          setActiveSkillIndex((index) => (index + 1) % matchingSkills.length)
          return
        }
        if (e.key === 'ArrowUp' && matchingSkills.length > 0) {
          e.preventDefault()
          setActiveSkillIndex((index) => (index - 1 + matchingSkills.length) % matchingSkills.length)
          return
        }
        if (e.key === 'Enter' && matchingSkills.length > 0) {
          e.preventDefault()
          insertSkill(matchingSkills[activeSkillIndex] ?? matchingSkills[0])
          return
        }
        if (e.key === 'Escape') {
          e.preventDefault()
          setMention(null)
          return
        }
      }
      // 支持 Ctrl/Cmd + Enter 发送
      if (!mention && e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault()
        handleSend()
      }
    }

    return (
    <div className={cn('runner-command-surface flex w-full flex-col py-3', className)}>
      {/* 顶部的文件列表 */}
      {files.length > 0 && (
        <div className="w-full px-4 mb-1">
          <ScrollArea className="w-full whitespace-nowrap">
            <div className="flex w-max space-x-4 pb-4">
              {files.map((file) => (
                <Item
                  key={file.id}
                  variant="muted"
                  className="p-2 flex-shrink-0 gap-2"
                >
                  {/* 左侧文件图标 */}
                  <ItemMedia>
                    <Avatar className="size-8">
                      <AvatarGroupCount>
                        <FileText/>
                      </AvatarGroupCount>
                    </Avatar>
                  </ItemMedia>
                  {/* 文件信息 */}
                  <ItemContent className="gap-0">
                    <ItemTitle className="text-sm text-gray-700">{file.filename}</ItemTitle>
                    <ItemDescription className="text-xs">
                      {file.extension} · {formatFileSize(file.size)}
                    </ItemDescription>
                  </ItemContent>
                  <ItemActions>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      className="cursor-pointer"
                      onClick={() => handleRemoveFile(file.id)}
                      disabled={uploading}
                      aria-label={`移除附件 ${file.filename}`}
                    >
                      <XCircle/>
                    </Button>
                  </ItemActions>
                </Item>
              ))}
            </div>
            <ScrollBar orientation="horizontal"/>
          </ScrollArea>
        </div>
      )}
      {/* 中间输入框 */}
      <div className="px-4 mb-3 relative">
        <textarea
          ref={textareaRef}
          rows={2}
          value={inputValue}
          onChange={handleInputChange}
          onKeyDown={handleKeyDown}
          placeholder="分配一个任务或提问任何问题..."
          className="scrollbar-hide h-[46px] min-h-[40px] w-full resize-none bg-transparent text-sm leading-relaxed outline-none placeholder:text-muted-foreground"
          disabled={sending || disabled || teamLocked}
        />
        {mention && !sending && !disabled && !teamLocked && (
          <div className="runner-floating-surface absolute left-4 right-4 top-full z-50 max-h-56 overflow-y-auto bg-popover p-1">
            {loadingSkills && (
              <div className="flex items-center gap-2 px-3 py-2 text-xs text-gray-500">
                <Loader2 className="size-3 animate-spin"/>
                正在加载 Skills
              </div>
            )}
            {!loadingSkills && matchingSkills.length === 0 && (
              <div className="px-3 py-2 text-xs text-gray-500">没有匹配的 Skill</div>
            )}
            {!loadingSkills && matchingSkills.map((skill, index) => (
              <button
                key={skill.id}
                type="button"
                className={cn(
                  'flex w-full flex-col rounded-sm px-3 py-2 text-left',
                  index === activeSkillIndex ? 'bg-secondary' : 'hover:bg-accent',
                )}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => insertSkill(skill)}
              >
                <span className="text-sm font-medium text-gray-700">${skill.name}</span>
                <span className="line-clamp-2 text-xs text-gray-500">{skill.description}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      {/* 底部上传&发送按钮 */}
      <footer className="flex flex-row justify-between w-full px-3">
        {/* 上传按钮 */}
        <div className="flex items-center gap-2">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={handleFileSelect}
            disabled={uploading || teamLocked}
          />
          <Button
            variant="outline"
            className="h-8 w-8 rounded-md cursor-pointer"
            onClick={handleUploadClick}
            disabled={uploading || teamLocked}
            aria-label="上传附件"
          >
            {uploading ? (
              <Loader2 className="size-4 animate-spin"/>
            ) : (
              <Paperclip/>
            )}
          </Button>
          <div role="group" aria-label="Agent 模式" className="flex rounded-full border border-border bg-secondary p-0.5">
            {([['react', '单 Agent'], ['team', '多 Agent']] as const).map(([value, label]) => (
              <Button
                key={value}
                type="button"
                size="sm"
                variant={mode === value ? 'default' : 'ghost'}
                className="h-8 rounded-full px-3 text-xs"
                disabled={isRunning || sending || disabled}
                onClick={() => onModeChange(value)}
                aria-pressed={mode === value}
              >
                {label}
              </Button>
            ))}
          </div>
        </div>
        {/* 发送/暂停按钮 */}
        <div className="flex gap-2">
          {isRunning ? (
            // 任务运行中时显示暂停按钮
            <Button
              variant="outline"
              className="h-8 w-8 rounded-md cursor-pointer"
              onClick={onStop}
              disabled={!onStop}
              aria-label="停止任务"
            >
              <Pause className="size-4" />
            </Button>
          ) : (
            // 任务未运行时显示发送按钮
            <Button
              variant="outline"
              className="h-8 w-8 rounded-md cursor-pointer"
              onClick={handleSend}
              disabled={sending || disabled || !inputValue.trim()}
              aria-label="发送消息"
            >
              {sending ? (
                <Loader2 className="size-4 animate-spin"/>
              ) : (
                <ArrowUp/>
              )}
            </Button>
          )}
        </div>
      </footer>
    </div>
    )
  }
)

ChatInput.displayName = 'ChatInput'
