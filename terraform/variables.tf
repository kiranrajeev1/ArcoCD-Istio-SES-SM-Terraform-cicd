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
