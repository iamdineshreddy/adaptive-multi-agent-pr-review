import { Route, Routes } from "react-router-dom";
import Layout from "./pages/Layout";
import DashboardPage from "./pages/DashboardPage";
import ReviewsPage from "./pages/ReviewsPage";
import ReviewDetailPage from "./pages/ReviewDetailPage";
import RepositoriesPage from "./pages/RepositoriesPage";
import MetricsPage from "./pages/MetricsPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="reviews" element={<ReviewsPage />} />
        <Route path="reviews/:reviewId" element={<ReviewDetailPage />} />
        <Route path="repositories" element={<RepositoriesPage />} />
        <Route path="metrics" element={<MetricsPage />} />
      </Route>
    </Routes>
  );
}