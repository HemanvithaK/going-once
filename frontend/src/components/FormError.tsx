/** An error message that screen readers announce as soon as it appears. */
export function FormError({ message }: { message: string | null }) {
  if (message === null) return null
  return (
    <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
      {message}
    </p>
  )
}
