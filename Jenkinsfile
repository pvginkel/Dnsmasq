// Builds the dnsmasq, dnsmasq-config-generator and dnsmasq-management-api images on every push,
// tests them by running the dnsmasq-validation image as a Kubernetes Job whose integration tier
// starts each stack it needs as a Job of its own, and pins the tested images into DnsmasqDeploy,
// which Argo CD syncs to prd.
//
// The three images are built before Test, since the integration tier runs against the very tags
// the pins name: a red build has still moved their latest.
//
// Controller config:
//   - Job: Dnsmasq/Dnsmasq
//   - SCM: pvginkel/Dnsmasq, branch main
//   - Script Path: Jenkinsfile

library identifier: 'JenkinsPipelineUtils', changelog: false

pipeline {
    agent {
        kubernetes {
            inheritFrom 'jenkins-agent kaniko'
            yamlMergeStrategy merge()
            yaml podYaml(templates: ['k8s'])
        }
    }

    options {
        disableConcurrentBuilds(abortPrevious: true)
        skipDefaultCheckout()
        timeout(time: 60, unit: 'MINUTES')
        timestamps()
    }

    triggers {
        githubPush()
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Build dnsmasq image') {
            steps {
                container('kaniko') {
                    script {
                        helmCharts.kaniko2(
                            context: '.',
                            dockerfile: 'dnsmasq/Dockerfile',
                            destinations: [
                                "registry:5000/dnsmasq:${currentBuild.number}",
                                'registry:5000/dnsmasq:latest',
                            ]
                        )
                    }
                }
            }
        }

        stage('Build dnsmasq-config-generator image') {
            steps {
                container('kaniko') {
                    script {
                        helmCharts.kaniko2(
                            context: '.',
                            dockerfile: 'config-generator/Dockerfile',
                            destinations: [
                                "registry:5000/dnsmasq-config-generator:${currentBuild.number}",
                                'registry:5000/dnsmasq-config-generator:latest',
                            ]
                        )
                    }
                }
            }
        }

        stage('Build dnsmasq-management-api image') {
            steps {
                container('kaniko') {
                    script {
                        helmCharts.kaniko2(
                            context: '.',
                            dockerfile: 'management-api/Dockerfile',
                            destinations: [
                                "registry:5000/dnsmasq-management-api:${currentBuild.number}",
                                'registry:5000/dnsmasq-management-api:latest',
                            ]
                        )
                    }
                }
            }
        }

        stage('Build dnsmasq-validation image') {
            steps {
                container('kaniko') {
                    script {
                        helmCharts.kaniko2(
                            context: '.',
                            dockerfile: 'validation/Dockerfile',
                            destinations: ["registry:5000/dnsmasq-validation:${currentBuild.number}"]
                        )
                    }
                }
            }
        }

        stage('Test') {
            steps {
                container('k8s') {
                    script {
                        String namespace = kubectl.currentNamespace()
                        String job = "dnsmasq-validation-${currentBuild.number}"
                        try {
                            kubectl.startJob("""\
                                apiVersion: batch/v1
                                kind: Job
                                metadata:
                                  name: ${job}
                                  labels:
                                    app.kubernetes.io/name: dnsmasq-validation
                                    app.kubernetes.io/managed-by: jenkins
                                    jenkins/build-number: "${currentBuild.number}"
                                spec:
                                  backoffLimit: 0
                                  activeDeadlineSeconds: 900
                                  ttlSecondsAfterFinished: 3600
                                  template:
                                    spec:
                                      restartPolicy: Never
                                      containers:
                                        - name: validation
                                          image: registry:5000/dnsmasq-validation:${currentBuild.number}
                                          imagePullPolicy: Always
                                          env:
                                            - name: STACK_NAMESPACE
                                              value: ${namespace}
                                            - name: DNSMASQ_IMAGE
                                              value: registry:5000/dnsmasq:${currentBuild.number}
                                            - name: CONFIG_GENERATOR_IMAGE
                                              value: registry:5000/dnsmasq-config-generator:${currentBuild.number}
                                            - name: MANAGEMENT_API_IMAGE
                                              value: registry:5000/dnsmasq-management-api:${currentBuild.number}
                                          resources:
                                            requests:
                                              cpu: 500m
                                """.stripIndent())
                            kubectl.waitForJobContainer(job, 'validation', namespace)
                            String pod = kubectl.getJobPodName(job, namespace)
                            kubectl.savePodLogs(pod, 'validation', namespace, 'validation-raw.log')

                            // validation/run.sh writes each suite's JUnit file into its log,
                            // base64-encoded between an ===JUNIT:<file>=== line and an
                            // ===JUNIT_END=== line.
                            sh '''
                                set -eu
                                mkdir -p test-results
                                awk '
                                    /^===JUNIT:.*===$/ {
                                        fname = $0
                                        sub(/^===JUNIT:/, "", fname)
                                        sub(/===$/, "", fname)
                                        content = ""
                                        capture = 1
                                        next
                                    }
                                    /^===JUNIT_END===$/ {
                                        print content | "base64 -d > test-results/" fname
                                        close("base64 -d > test-results/" fname)
                                        capture = 0
                                        next
                                    }
                                    capture { content = content (content ? "\\n" : "") $0 }
                                    !capture { print }
                                ' validation-raw.log > validation-stripped.log
                            '''
                            utils.cleanLog('validation-stripped.log', 'validation.log')
                            archiveArtifacts artifacts: 'validation.log, test-results/*.xml', allowEmptyArchive: true
                            junit testResults: 'test-results/*.xml', allowEmptyResults: true

                            // validation/run.sh writes one
                            // ===SUITE_RESULT:<suite>:<passed>:<failed>:<skipped>=== line per suite.
                            int suites = 0
                            int passed = 0
                            int failed = 0
                            int skipped = 0
                            String[] lines = readFile('validation.log').split('\n')
                            for (int i = 0; i < lines.length; i++) {
                                if (lines[i].startsWith('===SUITE_RESULT:')) {
                                    String[] counts = lines[i].replace('===SUITE_RESULT:', '').replace('===', '').split(':')
                                    suites++
                                    passed += Integer.parseInt(counts[1])
                                    failed += Integer.parseInt(counts[2])
                                    skipped += Integer.parseInt(counts[3])
                                }
                            }
                            if (suites > 0) {
                                currentBuild.description = "${passed} passed, ${failed} failed, ${skipped} skipped"
                            }

                            String exitCode = kubectl.getContainerExitCode(pod, 'validation', namespace)
                            if (!exitCode) {
                                String reason = kubectl.getJobFailReason(job, namespace)
                                error("Validation failed: no exit code (pod ${pod}${reason ? ", reason ${reason}" : ''})")
                            } else if (exitCode != '0') {
                                error("Validation failed: exit code ${exitCode}")
                            }
                        } finally {
                            kubectl.deleteJob(job, namespace)
                        }
                    }
                }
            }
        }

        stage('Write image pins') {
            steps {
                container('k8s') {
                    script {
                        cicd.writeVersionPins(repo: 'pvginkel/DnsmasqDeploy', pins: [
                            'config/prd/values.yaml': [
                                'images.dnsmasq': ":${currentBuild.number}",
                                'images.dnsmasqConfigGenerator': ":${currentBuild.number}",
                                'images.dnsmasqManagementApi': ":${currentBuild.number}",
                            ],
                        ])
                    }
                }
            }
        }
    }
}
