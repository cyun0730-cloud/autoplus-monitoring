// PM2 설정 파일 - 오토플러스 뉴스 모니터링 Flask 웹 대시보드 실행용
module.exports = {
  apps: [
    {
      name: 'autoplus-monitoring-web',
      script: 'python3',
      args: 'main.py --web',
      cwd: '/home/user/webapp/autoplus_monitoring',
      env: {
        PYTHONUNBUFFERED: '1'
      },
      watch: false,
      instances: 1,
      exec_mode: 'fork'
    }
  ]
}
