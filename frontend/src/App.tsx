import { useQuery } from '@tanstack/react-query'
import { configApi } from '@/lib/api'
import { Layout } from './components/Layout'
import { PortfolioTab } from './components/PortfolioTab'
import { ScannerTab } from './components/ScannerTab'
import { ManageTab } from './components/ManageTab'
import { SettingsTab } from './components/SettingsTab'
import { Tabs, TabsList, TabsTrigger, TabsContent } from './components/ui/tabs'

export default function App() {
  const { data: cfg } = useQuery({
    queryKey: ['config'],
    queryFn:  configApi.get,
    staleTime: 60_000,
  })

  return (
    <Layout mockMode={cfg?.mock_mode ?? false}>
      <Tabs defaultValue="portfolio">
        <TabsList className="mb-6">
          <TabsTrigger value="portfolio">Portfolio</TabsTrigger>
          <TabsTrigger value="scanner">Scanner</TabsTrigger>
          <TabsTrigger value="manage">Manage</TabsTrigger>
          <TabsTrigger value="settings">Settings</TabsTrigger>
        </TabsList>

        <TabsContent value="portfolio">
          <PortfolioTab />
        </TabsContent>
        <TabsContent value="scanner">
          <ScannerTab />
        </TabsContent>
        <TabsContent value="manage">
          <ManageTab />
        </TabsContent>
        <TabsContent value="settings">
          <SettingsTab />
        </TabsContent>
      </Tabs>
    </Layout>
  )
}
