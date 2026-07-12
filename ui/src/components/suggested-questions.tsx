'use client'

import {cn} from '@/lib/utils'
import {Button} from '@/components/ui/button'
import {suggestedQuestions} from '@/config/app.config'

interface SuggestedQuestionsProps {
  className?: string
  onQuestionClick?: (question: string) => void
}

export function SuggestedQuestions({className, onQuestionClick}: SuggestedQuestionsProps) {
  const handleClick = (question: string) => {
    onQuestionClick?.(question)
  }

  return (
    <div className={cn('flex flex-wrap gap-2 sm:gap-3', className)}>
      {suggestedQuestions.map((question, index) => (
        <Button
          key={index}
          variant="outline"
          className="h-auto min-h-9 cursor-pointer whitespace-normal break-words border-foreground/20 bg-card px-3 py-2 text-left text-xs hover:border-emphasis hover:bg-secondary sm:text-sm"
          onClick={() => handleClick(question)}
        >
          {question}
        </Button>
      ))}
    </div>
  )
}
