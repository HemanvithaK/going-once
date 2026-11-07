import { Route, Routes } from 'react-router'
import { Layout } from './components/Layout'
import { AuctionPage } from './pages/AuctionPage'
import { ListingsPage } from './pages/ListingsPage'
import { LoginPage } from './pages/LoginPage'
import { NotFoundPage } from './pages/NotFoundPage'
import { RegisterPage } from './pages/RegisterPage'

/** Which page to draw for which address. Every page sits inside the Layout. */
export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<ListingsPage />} />
        <Route path="auctions/:listingId" element={<AuctionPage />} />
        <Route path="login" element={<LoginPage />} />
        <Route path="register" element={<RegisterPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}
