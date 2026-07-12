'use client'

import {useEffect, useRef, useState} from 'react'
import {BookOpen, Eye, Loader2, Trash, Upload} from 'lucide-react'
import {toast} from 'sonner'

import {Badge} from '@/components/ui/badge'
import {Button} from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {FieldDescription, FieldGroup, FieldLegend, FieldSet} from '@/components/ui/field'
import {Item, ItemContent, ItemDescription, ItemGroup, ItemTitle} from '@/components/ui/item'
import {ScrollArea} from '@/components/ui/scroll-area'
import {Switch} from '@/components/ui/switch'
import {skillApi} from '@/lib/api'
import type {SkillDetail, SkillListItem} from '@/lib/api'


export function SkillSettings() {
  const [skills, setSkills] = useState<SkillListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [detail, setDetail] = useState<SkillDetail | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    skillApi.list()
      .then((data) => setSkills(data?.skills ?? []))
      .catch((error) => {
        toast.error(error instanceof Error ? error.message : '获取 Skill 列表失败')
      })
      .finally(() => setLoading(false))
  }, [])

  const replaceSkill = (next: SkillListItem) => {
    setSkills((current) => {
      const exists = current.some((skill) => skill.id === next.id)
      const updated = exists
        ? current.map((skill) => skill.id === next.id ? next : skill)
        : [...current, next]
      return updated.sort((a, b) => a.name.localeCompare(b.name))
    })
  }

  const handleUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      replaceSkill(await skillApi.upload(file))
      toast.success('Skill 已上传或覆盖')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Skill 上传失败')
    } finally {
      setUploading(false)
      event.target.value = ''
    }
  }

  const handleToggle = async (skill: SkillListItem, enabled: boolean) => {
    replaceSkill({...skill, enabled})
    try {
      replaceSkill(await skillApi.setEnabled(skill.id, enabled))
      toast.success(`${skill.name} 已${enabled ? '启用' : '禁用'}`)
    } catch (error) {
      replaceSkill(skill)
      toast.error(error instanceof Error ? error.message : '操作失败')
    }
  }

  const handleDelete = async (skill: SkillListItem) => {
    setSkills((current) => current.filter((item) => item.id !== skill.id))
    try {
      await skillApi.delete(skill.id)
      toast.success(`已删除 Skill「${skill.name}」`)
    } catch (error) {
      replaceSkill(skill)
      toast.error(error instanceof Error ? error.message : '删除失败')
    }
  }

  const handleDetail = async (skill: SkillListItem) => {
    try {
      setDetail(await skillApi.detail(skill.id))
      setDetailOpen(true)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '获取 Skill 详情失败')
    }
  }

  return (
    <div className="w-full px-1">
      <FieldGroup>
        <FieldSet>
          <FieldLegend className="w-full flex justify-between items-center text-lg font-bold text-gray-700">
            Skills
            <>
              <input
                ref={fileInputRef}
                type="file"
                accept=".zip,application/zip"
                className="hidden"
                onChange={handleUpload}
              />
              <Button
                type="button"
                size="xs"
                className="cursor-pointer"
                disabled={uploading}
                onClick={() => fileInputRef.current?.click()}
              >
                {uploading ? <Loader2 className="animate-spin"/> : <Upload/>}
                上传 Skill ZIP
              </Button>
            </>
          </FieldLegend>
          <FieldDescription className="text-sm">
            上传并管理系统全局 Skill。Agent 会按任务需要加载完整指令。
          </FieldDescription>

          {loading && (
            <div className="flex justify-center py-8">
              <Loader2 className="size-6 animate-spin text-muted-foreground"/>
            </div>
          )}

          {!loading && skills.length === 0 && (
            <div className="py-8 text-center text-sm text-muted-foreground">
              暂无 Skill，请上传 ZIP
            </div>
          )}

          {!loading && skills.length > 0 && (
            <ItemGroup className="gap-3">
              {skills.map((skill) => (
                <Item key={skill.id} variant="outline">
                  <ItemContent>
                    <ItemTitle className="w-full flex justify-between items-center text-md font-bold text-gray-700">
                      <div className="flex items-center gap-2 min-w-0">
                        <BookOpen className="size-4 shrink-0"/>
                        <span className="truncate">{skill.name}</span>
                        {!skill.enabled && <Badge>禁用</Badge>}
                      </div>
                      <div className="flex items-center gap-2">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-xs"
                          className="cursor-pointer"
                          aria-label={`查看 ${skill.name}`}
                          onClick={() => handleDetail(skill)}
                        >
                          <Eye/>
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-xs"
                          className="cursor-pointer"
                          aria-label={`删除 ${skill.name}`}
                          onClick={() => handleDelete(skill)}
                        >
                          <Trash/>
                        </Button>
                        <Switch
                          checked={skill.enabled}
                          onCheckedChange={(checked) => handleToggle(skill, checked)}
                        />
                      </div>
                    </ItemTitle>
                    <ItemDescription>{skill.description}</ItemDescription>
                  </ItemContent>
                </Item>
              ))}
            </ItemGroup>
          )}
        </FieldSet>
      </FieldGroup>

      <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
        <DialogContent className="!max-w-[760px]">
          <DialogHeader>
            <DialogTitle>{detail?.name ?? 'Skill'}</DialogTitle>
            <DialogDescription>{detail?.description}</DialogDescription>
          </DialogHeader>
          <ScrollArea className="h-[460px] rounded-lg border bg-gray-50">
            <pre className="p-4 text-xs whitespace-pre-wrap break-words text-gray-700">
              {detail?.skill_md ?? ''}
            </pre>
          </ScrollArea>
        </DialogContent>
      </Dialog>
    </div>
  )
}
