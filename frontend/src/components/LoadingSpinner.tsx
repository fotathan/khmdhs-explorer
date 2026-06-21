import { motion } from 'framer-motion'

interface LoadingSpinnerProps {
  text?: string
}

export default function LoadingSpinner({ text = 'Loading...' }: LoadingSpinnerProps) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="flex flex-col items-center justify-center py-16"
    >
      <div className="relative">
        <div className="w-10 h-10 border-3 border-slate-200 rounded-full" />
        <div className="absolute inset-0 w-10 h-10 border-3 border-teal-600 rounded-full border-t-transparent animate-spin" />
      </div>
      <p className="mt-4 text-sm text-slate-500">{text}</p>
    </motion.div>
  )
}
