# Istio-Argocd in Minikube cluster with terraform and Github Actions, Integreting AWS SES and Secrets Manager
### Prerequisites 🛠️

Ensure you have the following installed on your local machine:

  * **Minikube:** A tool that runs a single-node Kubernetes cluster.
  * **Kubectl:** The command-line tool for controlling Kubernetes clusters.
  * **Terraform:** An infrastructure-as-code tool.
  * **Git:** Version control system.
  * **AWS CLI:** For managing AWS resources.
  * **Docker:** To build and manage container images.
  * **Python 3.x:** To run the sample application.

You also need a GitHub repository and an AWS account.

-----

### Step 1: Manual Setup and AWS Configuration

This phase involves one-time manual steps to prepare the AWS account and GitHub repository for the automated workflow.

#### 1.1 Store Docker Hub Credentials in AWS Secrets Manager

1.  **Generate a Docker Hub Access Token:** Go to your Docker Hub account settings, navigate to the **Security** tab, and create a new **Access Token**.
2.  **Store the Secret:** In the AWS Management Console, go to **Secrets Manager**.
      * Click **Store a new secret**.
      * Choose **Other type of secret**.
      * Enter two key-value pairs:
          * **Key:** `username`, **Value:** Your Docker Hub username.
          * **Key:** `password`, **Value:** The access token you just generated.
      * Name the secret `dockerhub-credentials` and save it. Note down the full ARN of this secret.

#### 1.2 Configure AWS SES for Email Notifications

1.  **Verify an Email Address:** In the AWS Management Console, go to **Simple Email Service (SES)**.
      * Navigate to **Verified identities**.
      * Click **Create identity** and choose **Email address**.
      * Enter the email address you want to use for notifications and follow the verification steps. This is the sender's email address.
2.  **Request Production Access (if needed):** AWS SES is in a sandbox environment by default. You can only send emails to verified identities. If you need to send emails to unverified recipients, you must request a sending limit increase.

#### 1.3 Create an OIDC Provider and IAM Role for GitHub Actions

This is the most critical step for a secure, keyless workflow.

1.  **Create the OIDC Provider:**

    ```bash
    aws iam create-open-id-connect-provider \
      --url https://token.actions.githubusercontent.com \
      --client-id-list sts.amazonaws.com \
      --thumbprint-list <YOUR_PROVIDER_THUMBPRINT>
    ```

      * To get the thumbprint, use the `openssl` command on `token.actions.githubusercontent.com`.
      * Alternatively, you can create the provider via the AWS console by navigating to **IAM \> Identity providers \> Add provider**. Use `https://token.actions.githubusercontent.com` as the provider URL and `sts.amazonaws.com` as the audience.

2.  **Create the IAM Role:** Create an IAM role with a **Web Identity** trust policy. This policy allows GitHub Actions to assume the role.

      * **Trust Policy:**
        ```json
        {
          "Version": "2012-10-17",
          "Statement": [
            {
              "Effect": "Allow",
              "Principal": {
                "Federated": "arn:aws:iam::<YOUR_AWS_ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"
              },
              "Action": "sts:AssumeRoleWithWebIdentity",
              "Condition": {
                "StringEquals": {
                  "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
                },
                "StringLike": {
                  "token.actions.githubusercontent.com:sub": "repo:<YOUR_GITHUB_USERNAME>/<YOUR_REPO_NAME>:*"
                }
              }
            }
          ]
        }
        ```
      * Replace `<YOUR_AWS_ACCOUNT_ID>`, `<YOUR_GITHUB_USERNAME>`, and `<YOUR_REPO_NAME>`.

3.  **Attach Policies to the IAM Role:** Attach the necessary permissions to this role. You'll need policies for Secrets Manager and SES.

      * **Secrets Manager Policy:**

        ```json
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "secretsmanager:GetSecretValue",
                    "Resource": "arn:aws:secretsmanager:<YOUR_AWS_REGION>:<YOUR_AWS_ACCOUNT_ID>:secret:dockerhub-credentials-*"
                }
            ]
        }
        ```

      * **SES Policy:**

        ```json
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "ses:SendEmail",
                    "Resource": "*"
                }
            ]
        }
        ```

      * Attach the `AmazonSESFullAccess` managed policy for simplicity in this lab, but in a production environment, always follow the principle of least privilege.

      * Note the ARN of the IAM role you created. You'll use this in your GitHub Actions workflow.

-----

### Step 2: Application and Infrastructure Files

Create the following file structure in your GitHub repository.

#### 2.1 Sample Python Application: `app.py`

This is a simple Flask application.

```python
from flask import Flask
import os

app = Flask(__name__)

@app.route('/')
def hello():
    # Use environment variable to show a message
    message = os.environ.get('APP_MESSAGE', 'Hello from my Python app!')
    return f'<h1>{message}</h1>'

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
```

#### 2.2 Dockerfile: `Dockerfile`

This file builds a Docker image for the Python application.

```dockerfile
# Use a slim Python image
FROM python:3.9-slim-buster

# Set the working directory
WORKDIR /app

# Copy the application code
COPY . /app

# Install any dependencies (if any)
# For this simple app, no requirements.txt is needed
# If you had dependencies, you'd add:
# COPY requirements.txt .
# RUN pip install --no-cache-dir -r requirements.txt

# Expose the port the app runs on
EXPOSE 5000

# Run the application
CMD ["python", "app.py"]
```

#### 2.3 Terraform Scripts: `main.tf`, `variables.tf`, `providers.tf`

This is the core of the infrastructure. Terraform with the **minikube provider** is used to interact with your local cluster.

  * **`main.tf`**:

    ```terraform
    # Install ArgoCD via its Helm chart
    resource "helm_release" "argocd" {
      name       = "argocd"
      repository = "https://argoproj.github.io/argo-helm"
      chart      = "argo-cd"
      namespace  = "argocd"
      create_namespace = true
      values     = [file("values/argocd-values.yaml")]
    }

    # Install Istio via its Helm chart
    resource "helm_release" "istio" {
      name       = "istio-base"
      repository = "https://istio-release.storage.googleapis.com/charts"
      chart      = "base"
      namespace  = "istio-system"
      create_namespace = true
    }

    resource "helm_release" "istiod" {
      name       = "istiod"
      repository = "https://istio-release.storage.googleapis.com/charts"
      chart      = "istiod"
      namespace  = "istio-system"
      depends_on = [helm_release.istio]
    }

    # Create a Kubernetes namespace for the application
    resource "kubernetes_namespace" "app_namespace" {
      metadata {
        name = var.app_namespace
        labels = {
          "istio-injection" = "enabled"
        }
      }
    }

    # ArgoCD Application resource
    resource "kubernetes_manifest" "argocd_app" {
      manifest = {
        apiVersion = "argoproj.io/v1alpha1"
        kind       = "Application"
        metadata = {
          name      = "my-python-app"
          namespace = "argocd"
        }
        spec = {
          project = "default"
          source = {
            repoURL        = "https://github.com/${var.github_owner}/${var.github_repo}.git"
            targetRevision = "HEAD"
            path           = "manifests" # Path to your Kubernetes manifests
          }
          destination = {
            server    = "https://kubernetes.default.svc"
            namespace = var.app_namespace
          }
          syncPolicy = {
            automated = {
              prune = true
              selfHeal = true
            }
          }
        }
      }
    }
    ```

  * **`variables.tf`**:

    ```terraform
    variable "github_owner" {
      description = "Your GitHub username or organization."
      type        = string
    }

    variable "github_repo" {
      description = "The name of your GitHub repository."
      type        = string
    }

    variable "app_namespace" {
      description = "The Kubernetes namespace for the application."
      type        = string
      default     = "dev"
    }
    ```

  * **`providers.tf`**:

    ```terraform
    # This block configures the Kubernetes provider to use the minikube context
    terraform {
      required_providers {
        kubernetes = {
          source  = "hashicorp/kubernetes"
          version = "2.21.1"
        }
        helm = {
          source  = "hashicorp/helm"
          version = "2.10.1"
        }
      }
    }
    provider "kubernetes" {
      config_path = "~/.kube/config"
    }

    provider "helm" {
      kubernetes {
        config_path = "~/.kube/config"
      }
    }
    ```

  * **`manifests/app.yaml`**: This manifest describes your application's deployment. ArgoCD will use this file.

    ```yaml
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: my-python-app
      namespace: dev
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: my-python-app
      template:
        metadata:
          labels:
            app: my-python-app
        spec:
          containers:
          - name: my-python-app
            image: docker.io/<YOUR_DOCKERHUB_USERNAME>/<YOUR_REPO_NAME>:<TAG>
            ports:
            - containerPort: 5000
    ```

      * Replace `<YOUR_DOCKERHUB_USERNAME>/<YOUR_REPO_NAME>:<TAG>` with your actual image details. The GitHub Actions workflow will update the image tag.

-----

### Step 3: GitHub Actions Workflow

This workflow automates the entire process, triggered by a push to the `main` branch.

#### `/.github/workflows/deploy.yml`

```yaml
name: CI/CD Pipeline with Minikube, Terraform, ArgoCD, and Istio

on:
  push:
    branches:
      - main

# Grant the GHA runner permissions to get an OIDC token
permissions:
  id-token: write
  contents: read

jobs:
  deploy:
    runs-on: ubuntu-latest
    
    steps:
      - name: Checkout repository
        uses: actions/checkout@v3

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v3
        with:
          role-to-assume: arn:aws:iam::<YOUR_AWS_ACCOUNT_ID>:role/<YOUR_IAM_ROLE_NAME>
          aws-region: <YOUR_AWS_REGION>

      - name: Install Minikube
        run: |
          curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64
          sudo install minikube-linux-amd64 /usr/local/bin/minikube
          minikube start --driver=docker --cpus 4 --memory 8192

      - name: Wait for Minikube to be ready
        run: |
          minikube kubectl -- get pods -A

      - name: Get Docker Hub credentials from AWS Secrets Manager
        id: dockerhub-secret
        uses: aws-actions/aws-secretsmanager-get-secret@v1
        with:
          secret-id: dockerhub-credentials
          parse-json-secrets: true

      - name: Login to Docker Hub
        run: docker login -u ${{ steps.dockerhub-secret.outputs.username }} -p ${{ steps.dockerhub-secret.outputs.password }}

      - name: Build and push Docker image
        id: docker_build
        run: |
          IMAGE_TAG=$(echo $GITHUB_SHA | cut -c1-8)
          docker build . -t <YOUR_DOCKERHUB_USERNAME>/my-python-app:$IMAGE_TAG
          docker push <YOUR_DOCKERHUB_USERNAME>/my-python-app:$IMAGE_TAG
          echo "IMAGE_TAG=$IMAGE_TAG" >> $GITHUB_ENV

      - name: Update Kubernetes manifest with new image tag
        run: |
          sed -i "s|<TAG>|${{ env.IMAGE_TAG }}|g" manifests/app.yaml

      - name: Setup Terraform
        uses: hashicorp/setup-terraform@v2
        with:
          terraform_version: 1.5.0

      - name: Terraform Init
        run: terraform init

      - name: Terraform Apply
        run: terraform apply -auto-approve -var="github_owner=<YOUR_GITHUB_USERNAME>" -var="github_repo=<YOUR_REPO_NAME>"

      - name: Get ArgoCD initial password
        id: argocd-password
        run: |
          sleep 60
          ARGOCD_PASS=$(minikube kubectl -- -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d)
          echo "ARGOCD_PASSWORD=$ARGOCD_PASS" >> $GITHUB_ENV

      - name: Send email notification
        run: |
          aws ses send-email \
            --from "<VERIFIED_SES_EMAIL>" \
            --destination "ToAddresses=<RECIPIENT_EMAIL>" \
            --message file://<(echo '{"Subject": {"Data": "Deployment Succeeded"}, "Body": {"Text": {"Data": "Terraform and ArgoCD deployment succeeded. ArgoCD admin password: ${{ env.ARGOCD_PASSWORD }}"}}}')
```

-----

### Step 4: Execution and Verification

1.  **Commit and Push:** Add all the created files to your Git repository and push them to the `main` branch.

2.  **Monitor the GitHub Actions:** Go to the **Actions** tab in your GitHub repository and watch the workflow run. The steps will execute sequentially:

      * **Configure AWS credentials:** The OIDC provider and IAM role will grant temporary credentials to the runner.
      * **Login to Docker Hub:** The workflow will fetch credentials from AWS Secrets Manager and log in.
      * **Build and push Docker image:** The Python app image will be built and pushed to Docker Hub with a unique tag.
      * **Terraform Apply:** Terraform will provision the Helm releases for ArgoCD and Istio on your Minikube cluster and create the application deployment.
      * **Send email notification:** After a successful deployment, AWS SES will send an email with the ArgoCD admin password to the specified recipient.

3.  **Verify the deployment:**

      * After the workflow completes, check your email for the ArgoCD password.
      * On your local machine, run `minikube tunnel` in a separate terminal to expose the services.
      * You can access the ArgoCD UI by port-forwarding the service: `kubectl -n argocd port-forward svc/argocd-server 8080:443`.
      * Navigate to `https://localhost:8080` in your browser. Log in with the username `admin` and the password from the email. You'll see your `my-python-app` application deployed and healthy.
      * You can also verify the Istio sidecar injection by running `kubectl -n dev get pods`. You should see `2/2` containers, indicating the Istio proxy is running alongside your app.
