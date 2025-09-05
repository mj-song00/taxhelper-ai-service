package texhelper.chatservice.common.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;
import texhelper.chatservice.domain.law.dto.request.LawRequest;

@Configuration
public class RestClientConfig {

    @Bean
    public RestTemplate restTemplate() {
        return new RestTemplate();
    }

    @Bean
    public ObjectMapper objectMapper() {
        return new ObjectMapper();
    }

    @Value("{api.url}")
    private String urls;


    public LawRequest fetchLaw(RestTemplate restTemplate, ObjectMapper objectMapper, String mst) {
        String url = urls + mst + "&type=JSON";
        try {
            String result = restTemplate.getForObject(url, String.class);
            return objectMapper.readValue(result, LawRequest.class);
        } catch (RestClientException e) {
            throw new RuntimeException("법령 API 호출 실패", e);
        } catch (Exception e) {
            throw new RuntimeException("JSON 매핑 실패", e);
        }
    }




}
