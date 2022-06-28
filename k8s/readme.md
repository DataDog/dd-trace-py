Build and push the init image:

```
cd auto-inst
docker build . --tag ddkverhoog/k8s-auto-init-py:latest
```

```
docker push ddkverhoog/k8s-auto-init-py:latest
```


# Running and testing in k8s

```
kubectl delete pod sample-app
kubectl apply -f pod.yml

kubectl logs -f sample-app -c app
kubectl logs -f sample-app -c agent

kubectl exec --stdin --tty sample-app -- /bin/bash
   # curl localhost:8000
   # curl localhost:8126/test/traces
```
