import { Link } from 'react-router'

export function NotFoundPage() {
  return (
    <div>
      <h1 className="text-2xl font-bold">Page not found</h1>
      <Link to="/" className="mt-2 inline-block text-indigo-700 hover:underline">
        Back to the auctions
      </Link>
    </div>
  )
}
